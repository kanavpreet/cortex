"""Tests for the Incident.io webhook transformer."""

from datetime import UTC, datetime
from math import floor
from typing import Any
from unittest.mock import patch

from svix.webhooks import Webhook, WebhookVerificationError

from chronicler.transformers.incidentio import (
    IncidentIOTransformer,
)

_WHSEC = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"


def _incident_object(
    incident_id: str = "01FDAG4SAP5TYPT98WGR2N7W91",
    reference: str = "INC-123",
    name: str | None = "Our database is sad",
    summary: str | None = "Our database is really really sad",
    severity: str = "Minor",
    status: str = "Closed",
    status_category: str = "closed",
    custom_fields: list[dict[str, Any]] | None = None,
    timestamp_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": incident_id,
        "reference": reference,
        "severity": {"name": severity},
        "incident_status": {"name": status, "category": status_category},
        "slack_channel_id": "C02AW36C1M5",
        "visibility": "public",
        "name": name,
        "summary": summary,
        "created_at": "2026-05-19T13:28:57.801578Z",
        "updated_at": "2026-05-19T14:00:00.000000Z",
        "incident_timestamp_values": timestamp_values or [],
        "custom_field_entries": custom_fields or [],
    }


def _webhook_payload(
    event_type: str = "public_incident.incident_created_v2",
    incident: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        event_type: incident if incident is not None else _incident_object(),
    }


class TestShouldProcess:
    def test_created_is_accepted(self) -> None:
        t = IncidentIOTransformer()
        assert t.should_process("", _webhook_payload()) is True

    def test_updated_is_accepted(self) -> None:
        t = IncidentIOTransformer()
        assert (
            t.should_process(
                "",
                _webhook_payload(event_type="public_incident.incident_updated_v2"),
            )
            is True
        )

    def test_status_changed_is_filtered(self) -> None:
        t = IncidentIOTransformer()
        assert (
            t.should_process(
                "",
                _webhook_payload(
                    event_type="public_incident.incident_status_changed_v2"
                ),
            )
            is False
        )

    def test_closed_is_filtered(self) -> None:
        t = IncidentIOTransformer()
        assert (
            t.should_process(
                "",
                _webhook_payload(event_type="public_incident.incident_closed_v2"),
            )
            is False
        )

    def test_missing_event_type_is_filtered(self) -> None:
        t = IncidentIOTransformer()
        assert t.should_process("", {}) is False


class TestToBaseMessage:
    def test_core_field_mapping(self) -> None:
        t = IncidentIOTransformer()
        msg = t.to_base_message(_webhook_payload())
        assert msg.source_type == "incidentio"
        assert msg.message_type == "base"
        assert msg.data["incident_id"] == "01FDAG4SAP5TYPT98WGR2N7W91"
        assert msg.data["reference_id"] == "INC-123"
        assert msg.data["severity"] == "Minor"
        assert msg.data["status"] == "Closed"
        assert msg.data["status_category"] == "closed"
        assert msg.data["visibility"] == "public"
        # Enricher fills these; chronicler leaves them None.
        assert msg.data["description_summary"] is None
        assert msg.data["root_cause_summary"] is None
        # `id` (DB auto-increment) is excluded from the data envelope.
        assert "id" not in msg.data

    def test_description_hash_is_none(self) -> None:
        """Hashes are computed by the enricher, not the chronicler."""
        t = IncidentIOTransformer()
        msg = t.to_base_message(_webhook_payload())
        assert msg.data["description_hash"] is None

    def test_root_cause_hash_is_none(self) -> None:
        """Hashes are computed by the enricher, not the chronicler."""
        t = IncidentIOTransformer()
        incident = _incident_object(
            custom_fields=[
                {
                    "custom_field": {"name": "Resolution Statement"},
                    "values": [{"value_text": "rolled back"}],
                }
            ]
        )
        msg = t.to_base_message(_webhook_payload(incident=incident))
        assert msg.data["root_cause_summary_hash"] is None

    def test_hashes_none_when_all_empty(self) -> None:
        t = IncidentIOTransformer()
        incident = _incident_object(name=None, summary=None)
        msg = t.to_base_message(_webhook_payload(incident=incident))
        assert msg.data["description_hash"] is None
        assert msg.data["root_cause_summary_hash"] is None


class TestToEnrichmentRequest:
    def test_includes_name_and_summary_when_present(self) -> None:
        t = IncidentIOTransformer()
        req = t.to_enrichment_request(_webhook_payload(), "task-1")
        assert req is not None
        assert req.source_type == "incidentio"
        assert req.producer == "chronicler"
        assert req.task_id == "task-1"
        assert req.entity_id == {"incident_id": "01FDAG4SAP5TYPT98WGR2N7W91"}
        assert req.content == {
            "name": "Our database is sad",
            "summary": "Our database is really really sad",
            "reference_id": "INC-123",
        }

    def test_includes_resolution_statement_when_present(self) -> None:
        t = IncidentIOTransformer()
        incident = _incident_object(
            custom_fields=[
                {
                    "custom_field": {"name": "Resolution Statement"},
                    "values": [{"value_text": "scaled the read replica"}],
                }
            ]
        )
        req = t.to_enrichment_request(_webhook_payload(incident=incident), "t")
        assert req is not None
        assert req.content["resolution_statement"] == "scaled the read replica"

    def test_returns_none_when_no_enrichable_content(self) -> None:
        t = IncidentIOTransformer()
        incident = _incident_object(name=None, summary=None)
        req = t.to_enrichment_request(_webhook_payload(incident=incident), "t")
        assert req is None


class TestValidateSignature:
    def _make_headers(self, body: bytes) -> dict[str, str]:
        now = datetime.now(UTC)
        sig = Webhook(_WHSEC).sign("msg_test_1", now, body.decode())
        return {
            "webhook-id": "msg_test_1",
            "webhook-timestamp": str(floor(now.timestamp())),
            "webhook-signature": sig,
        }

    def test_valid_signature_returns_true(self) -> None:
        t = IncidentIOTransformer()
        body = b'{"event_type":"public_incident.incident_created_v2"}'
        headers = self._make_headers(body)
        assert t.validate_signature(body, headers, _WHSEC) is True

    def test_tampered_body_returns_false(self) -> None:
        t = IncidentIOTransformer()
        body = b'{"event_type":"public_incident.incident_created_v2"}'
        headers = self._make_headers(body)
        tampered = b'{"event_type":"public_incident.incident_updated_v2"}'
        assert t.validate_signature(tampered, headers, _WHSEC) is False

    def test_missing_headers_returns_false(self) -> None:
        t = IncidentIOTransformer()
        assert t.validate_signature(b"{}", {}, _WHSEC) is False

    def test_sdk_exception_returns_false(self) -> None:
        """If the SDK raises for any reason, validate_signature must swallow
        the exception and return False — never bubble."""
        t = IncidentIOTransformer()
        with patch("chronicler.transformers.incidentio.Webhook") as mock_wh:
            mock_wh.return_value.verify.side_effect = WebhookVerificationError("nope")
            assert t.validate_signature(b"{}", {"webhook-id": "x"}, _WHSEC) is False


class TestEventTypeKeyExtraction:
    """The webhook envelope uses event_type as the key holding the incident."""

    def test_unknown_event_type_yields_empty_dict(self) -> None:
        # should_process gates this, but verify defense in depth: if a
        # filtered event slipped past, to_base_message would still produce
        # an empty record rather than crash.
        t = IncidentIOTransformer()
        payload = {"event_type": "public_incident.something_unrelated"}
        msg = t.to_base_message(payload)
        assert msg.data["incident_id"] == ""
        assert msg.data["reference_id"] == ""
