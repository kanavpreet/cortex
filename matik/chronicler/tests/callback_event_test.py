"""Tests for CallbackEvent model parsing + Thrift two-layer deserialization."""

import json

import pytest
from pydantic import ValidationError

from chronicler.models._thrift_binary import T_I64, T_MAP, T_STRING
from chronicler.models.callback_event import (
    CallbackEvent,
    CallbackEventDeserializationError,
    deserialize_record_value,
)
from chronicler.tests._thrift_encoder import encode_struct


class TestCallbackEvent:
    def test_parse_full_event(self) -> None:
        """Parse a realistic Yoyo Kafka event."""
        data = {
            "schema": "com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4",
            "external_service_type": "matik_github_webhook_events",
            "http_method": 3,
            "host": "callbacks.airbnb.com",
            "path": "/v1/external/callback/matik/github/webhook/events",
            "headers": {
                "x-github-event": "pull_request",
                "x-hub-signature-256": "sha256=abc123",
                "content-type": "application/json",
            },
            "payload": json.dumps({"action": "closed", "pull_request": {"id": 1}}),
            "request_received_ts": 1778583472017,
        }
        event = CallbackEvent.model_validate(data)
        assert event.external_service_type == "matik_github_webhook_events"
        assert event.provider == "github"
        assert event.headers["x-github-event"] == "pull_request"
        assert json.loads(event.payload)["action"] == "closed"
        assert event.request_received_ts == 1778583472017
        assert event.schema_ == "com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4"

    def test_provider_mapping_github(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_github_webhook_events",
            payload="{}",
        )
        assert event.provider == "github"

    def test_provider_mapping_jira(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_jira_webhook_events",
            payload="{}",
        )
        assert event.provider == "jira"

    def test_provider_mapping_incidentio(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_incidentio_webhook_events",
            payload="{}",
        )
        assert event.provider == "incidentio"

    def test_provider_mapping_matik_generic(self) -> None:
        """Production/staging's registered external_service_type."""
        event = CallbackEvent(
            external_service_type="matik_generic_webhook_events",
            payload="{}",
        )
        assert event.provider == "matik"

    def test_sandbox_generic_token_maps_to_matik(self) -> None:
        """Sandbox's registered external_service_type."""
        event = CallbackEvent(
            external_service_type="matik_sandbox_generic_webhook_events",
            payload="{}",
        )
        assert event.provider == "matik"

    def test_unknown_external_service_type_falls_through(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_unknown_events",
            payload="{}",
        )
        assert event.provider == "matik_unknown_events"

    def test_sandbox_ghe_token_maps_to_github(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_sandbox_ghe_webhook_events",
            payload="{}",
        )
        assert event.provider == "github"

    def test_sandbox_jira_token_maps_to_jira(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_sandbox_jira_webhook_events",
            payload="{}",
        )
        assert event.provider == "jira"

    def test_sandbox_incidentio_token_maps_to_incidentio(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_sandbox_incidentio_webhook_events",
            payload="{}",
        )
        assert event.provider == "incidentio"

    def test_minimal_event_only_required_fields(self) -> None:
        event = CallbackEvent(
            external_service_type="matik_github_webhook_events",
            payload='{"action": "test"}',
        )
        assert event.headers == {}
        assert event.schema_ is None
        assert event.http_method is None
        assert event.host is None
        assert event.path is None
        assert event.request_received_ts is None

    def test_missing_external_service_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            CallbackEvent.model_validate({"payload": "{}"})

    def test_missing_payload_raises(self) -> None:
        with pytest.raises(ValidationError):
            CallbackEvent.model_validate(
                {"external_service_type": "matik_github_webhook_events"}
            )


def _build_raw_message(
    *,
    external_service_type: str = "matik_sandbox_jira_webhook_events",
    headers: dict[str, str] | None = None,
    payload: str = '{"webhookEvent": "jira:issue_updated"}',
    request_received_ts: int = 1778600000000,
    include_schema: bool = True,
) -> bytes:
    """Encode a Yoyo-shaped RawMessage->CallbackEvent two-layer Thrift blob."""
    headers = headers if headers is not None else {"content-type": "application/json"}
    cb_fields: list[tuple[int, int, object]] = [
        (1, T_STRING, external_service_type),
        (6, T_MAP, headers),
        (7, T_STRING, payload.encode("utf-8")),
        (11, T_I64, request_received_ts),
    ]
    if include_schema:
        cb_fields.append(
            (31337, T_STRING, "com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4")
        )
    callback_event_bytes = encode_struct(cb_fields)
    return encode_struct([(2, T_STRING, callback_event_bytes)])


class TestDeserializeRecordValue:
    def test_round_trips_a_minimal_event(self) -> None:
        blob = _build_raw_message()
        ev = deserialize_record_value(blob)
        assert ev.external_service_type == "matik_sandbox_jira_webhook_events"
        assert ev.provider == "jira"
        assert ev.headers == {"content-type": "application/json"}
        assert json.loads(ev.payload)["webhookEvent"] == "jira:issue_updated"
        assert ev.request_received_ts == 1778600000000

    def test_decodes_github_topic_with_lowercased_headers(self) -> None:
        blob = _build_raw_message(
            external_service_type="matik_sandbox_ghe_webhook_events",
            headers={
                "x-github-event": "pull_request",
                "x-hub-signature-256": "sha256=abc",
            },
            payload='{"action": "closed"}',
        )
        ev = deserialize_record_value(blob)
        assert ev.provider == "github"
        assert ev.headers["x-github-event"] == "pull_request"

    def test_pulls_schema_field_31337(self) -> None:
        blob = _build_raw_message(include_schema=True)
        ev = deserialize_record_value(blob)
        assert ev.schema_ == "com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4"

    def test_truncated_envelope_raises(self) -> None:
        blob = _build_raw_message()
        with pytest.raises(CallbackEventDeserializationError, match="RawMessage"):
            deserialize_record_value(blob[:5])

    def test_missing_raw_event_field_raises(self) -> None:
        # RawMessage with only field 1 (a string standing in for metadata), no raw_event.
        bogus = encode_struct([(1, T_STRING, "metadata-placeholder")])
        with pytest.raises(CallbackEventDeserializationError, match="raw_event"):
            deserialize_record_value(bogus)

    def test_missing_external_service_type_raises(self) -> None:
        cb = encode_struct(
            [
                (7, T_STRING, b'{"x": 1}'),
                (11, T_I64, 1778600000000),
            ]
        )
        blob = encode_struct([(2, T_STRING, cb)])
        with pytest.raises(
            CallbackEventDeserializationError, match="external_service_type"
        ):
            deserialize_record_value(blob)

    def test_missing_payload_raises(self) -> None:
        cb = encode_struct([(1, T_STRING, "matik_sandbox_jira_webhook_events")])
        blob = encode_struct([(2, T_STRING, cb)])
        with pytest.raises(CallbackEventDeserializationError, match="payload"):
            deserialize_record_value(blob)

    def test_non_utf8_payload_raises(self) -> None:
        cb = encode_struct(
            [
                (1, T_STRING, "matik_sandbox_jira_webhook_events"),
                (7, T_STRING, b"\xff\xfe\x00\x80invalid"),
            ]
        )
        blob = encode_struct([(2, T_STRING, cb)])
        with pytest.raises(CallbackEventDeserializationError, match="UTF-8"):
            deserialize_record_value(blob)
