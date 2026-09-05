"""Tests for the Kafka consumer loop."""

import hashlib
import hmac
import json
import signal
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chronicler.consumer import (
    KafkaConsumerLoop,
    _get_header_ci,
    build_consumer,
    install_signal_handlers,
)
from chronicler.models._thrift_binary import T_I64, T_MAP, T_STRING
from chronicler.tests._thrift_encoder import encode_struct
from common.models.chronicler_config import (
    ChroniclerConfig,
    ChroniclerKafkaConfig,
)
from common.utils.github_utils import build_ghe_pr_content


def _encode_callback_event(
    external_service_type: str,
    headers: dict[str, str],
    payload_json: str,
    request_received_ts: int = 1778600000000,
) -> bytes:
    """Build a RawMessage->CallbackEvent two-layer Thrift blob like Yoyo emits."""
    callback_event = encode_struct(
        [
            (1, T_STRING, external_service_type),
            (6, T_MAP, headers),
            (7, T_STRING, payload_json.encode("utf-8")),
            (11, T_I64, request_received_ts),
        ]
    )
    # RawMessage.metadata (field 1) is required but the decoder skips it,
    # so we encode it as an empty struct via a tiny inline placeholder. The
    # outermost wrapper carries only field 2 (raw_event = callback_event bytes)
    # which is the only field chronicler reads.
    raw_message = encode_struct([(2, T_STRING, callback_event)])
    return raw_message


def _make_pr_payload(
    merged: bool = True, body: str | None = "Fix a bug"
) -> dict[str, Any]:
    return {
        "action": "closed",
        "pull_request": {
            "id": 123,
            "number": 1,
            "title": "Test PR",
            "merged": merged,
            "state": "closed",
            "locked": False,
            "created_at": "2026-04-01T10:00:00Z",
            "closed_at": "2026-04-02T14:30:00Z",
            "merged_at": "2026-04-02T14:30:00Z",
            "body": body,
            "base": {"ref": "main"},
        },
        "repository": {"id": 456, "full_name": "airbnb/matik"},
    }


def _gh_sig(body: str, secret: str) -> str:
    return (
        "sha256="
        + hmac.new(secret.encode(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    )


def _make_callback_event(
    external_service_type: str = "matik_github_webhook_events",
    payload: dict[str, Any] | None = None,
    event_type: str = "pull_request",
    secret: str | None = None,
    override_signature: str | None = None,
) -> bytes:
    """Build Kafka record bytes matching Yoyo's two-layer Thrift wire format."""
    payload = payload if payload is not None else _make_pr_payload()
    payload_json = json.dumps(payload)
    headers: dict[str, str] = {"x-github-event": event_type}
    if override_signature is not None:
        headers["x-hub-signature-256"] = override_signature
    elif secret is not None:
        headers["x-hub-signature-256"] = _gh_sig(payload_json, secret)
    return _encode_callback_event(
        external_service_type=external_service_type,
        headers=headers,
        payload_json=payload_json,
    )


def _make_kafka_msg(
    value: bytes | None,
    topic: str = "yoyo.callback.matik_github_webhook_events",
    error: object | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.value.return_value = value
    msg.topic.return_value = topic
    msg.partition.return_value = 0
    msg.offset.return_value = 1
    msg.error.return_value = error
    return msg


def _make_loop(
    scribe: MagicMock | None = None,
    enricher: MagicMock | None = None,
    chronicler_config: ChroniclerConfig | None = None,
    metrics: MagicMock | None = None,
) -> tuple[KafkaConsumerLoop, MagicMock]:
    consumer = MagicMock()
    kafka_config = ChroniclerKafkaConfig(
        bootstrap_servers="kafka-prod-a.kafka-prod-a:9092",
        topics=["yoyo.callback.matik_github_webhook_events"],
    )
    loop = KafkaConsumerLoop(
        consumer=consumer,
        kafka_config=kafka_config,
        scribe_publisher=scribe or MagicMock(send=AsyncMock()),
        enrichment_publisher=enricher,
        chronicler_config=chronicler_config,
        metrics=metrics,
    )
    return loop, consumer


class TestGetHeaderCI:
    def test_exact_match(self) -> None:
        assert (
            _get_header_ci({"X-GitHub-Event": "pull_request"}, "X-GitHub-Event")
            == "pull_request"
        )

    def test_lowercase_match(self) -> None:
        assert (
            _get_header_ci({"x-github-event": "pull_request"}, "X-GitHub-Event")
            == "pull_request"
        )

    def test_missing_returns_empty(self) -> None:
        assert _get_header_ci({}, "X-GitHub-Event") == ""


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_merged_pr_publishes_to_both_queues(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_merged_pr_enrichment_includes_fetched_comments(self) -> None:
        """When the github transformer has a GHE client, fetched comments are
        folded into the combined original_description."""
        from chronicler.transformers.base import get_transformer, register_transformer
        from chronicler.transformers.github_pr import GitHubPRTransformer

        payload = _make_pr_payload()
        # augment_enrichment_content needs repo name + org/owner login to fetch.
        payload["repository"] = {
            "id": 456,
            "name": "matik",
            "full_name": "airbnb/matik",
            "owner": {"id": 11111, "login": "airbnb"},
        }
        payload["organization"] = {"id": 11111, "login": "airbnb"}

        ghe_client = MagicMock()
        ghe_client.get_pr_comments = AsyncMock(return_value=["please fix", "ok now"])
        original = get_transformer("github")
        register_transformer("github", GitHubPRTransformer(ghe_client=ghe_client))
        try:
            scribe = MagicMock(send=AsyncMock())
            enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
            loop, _consumer = _make_loop(scribe=scribe, enricher=enricher)

            msg = _make_kafka_msg(_make_callback_event(payload=payload))
            await loop._handle_message(msg)

            enricher.publish.assert_awaited_once()
            req = enricher.publish.await_args.args[0]
            assert req.content == {
                "original_description": build_ghe_pr_content(
                    "Test PR", "Fix a bug", ["please fix", "ok now"]
                )
            }
            ghe_client.get_pr_comments.assert_awaited_once_with("airbnb", "matik", 1)
        finally:
            register_transformer("github", original)

    @pytest.mark.asyncio
    async def test_comment_fetch_failure_still_publishes_description(self) -> None:
        """A comment-fetch error must not block the description-only enrichment."""
        from chronicler.transformers.base import get_transformer, register_transformer
        from chronicler.transformers.github_pr import GitHubPRTransformer

        payload = _make_pr_payload()
        payload["repository"] = {
            "id": 456,
            "name": "matik",
            "full_name": "airbnb/matik",
            "owner": {"id": 11111, "login": "airbnb"},
        }
        payload["organization"] = {"id": 11111, "login": "airbnb"}

        ghe_client = MagicMock()
        ghe_client.get_pr_comments = AsyncMock(side_effect=RuntimeError("boom"))
        original = get_transformer("github")
        register_transformer("github", GitHubPRTransformer(ghe_client=ghe_client))
        try:
            scribe = MagicMock(send=AsyncMock())
            enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
            loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

            msg = _make_kafka_msg(_make_callback_event(payload=payload))
            await loop._handle_message(msg)

            enricher.publish.assert_awaited_once()
            req = enricher.publish.await_args.args[0]
            # Fetch failed → the comment-less combined field is published as-is.
            assert req.content == {
                "original_description": build_ghe_pr_content("Test PR", "Fix a bug", [])
            }
            consumer.commit.assert_called_once_with(msg, asynchronous=False)
        finally:
            register_transformer("github", original)

    @pytest.mark.asyncio
    async def test_non_merged_pr_is_filtered(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(
            _make_callback_event(payload=_make_pr_payload(merged=False))
        )
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        enricher.publish.assert_not_awaited()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_empty_body_still_enriches_via_title(self) -> None:
        """An empty PR body no longer skips enrichment: the title (and, with a
        GHE client, the comment thread) are still summarized. Regression for
        PRs like 'Update README.md' that have no body but do have comments."""
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(_make_callback_event(payload=_make_pr_payload(body="")))
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        req = enricher.publish.await_args.args[0]
        assert req.content == {
            "original_description": build_ghe_pr_content("Test PR", "", [])
        }
        consumer.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_provider_is_discarded(self) -> None:
        loop, consumer = _make_loop()

        msg = _make_kafka_msg(
            _make_callback_event(external_service_type="matik_unknown_events")
        )
        await loop._handle_message(msg)

        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_empty_message_is_discarded(self) -> None:
        loop, consumer = _make_loop()

        msg = _make_kafka_msg(None)
        await loop._handle_message(msg)

        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_malformed_envelope_is_discarded(self) -> None:
        loop, consumer = _make_loop()

        msg = _make_kafka_msg(b"not valid json {{{")
        await loop._handle_message(msg)

        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_malformed_inner_payload_is_discarded(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        loop, consumer = _make_loop(scribe=scribe)

        # Valid Thrift envelope but the webhook payload string is not JSON.
        bad = _encode_callback_event(
            external_service_type="matik_github_webhook_events",
            headers={"x-github-event": "pull_request"},
            payload_json="not valid json {{{",
        )
        msg = _make_kafka_msg(bad)
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_enrichment_publish_failure_does_not_commit(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value=None))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(_make_callback_event())
        with pytest.raises(RuntimeError):
            await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        consumer.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_scribe_failure_does_not_commit(self) -> None:
        scribe = MagicMock(send=AsyncMock(side_effect=Exception("SQS down")))
        enricher = MagicMock(publish=AsyncMock())
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(_make_callback_event())
        with pytest.raises(Exception, match="SQS down"):
            await loop._handle_message(msg)

        enricher.publish.assert_not_awaited()
        consumer.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_enrichment_publisher_still_processes(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        loop, consumer = _make_loop(scribe=scribe, enricher=None)

        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_matik_webhook_none_base_message_skips_scribe(self) -> None:
        """The matik/incident_channel_summary transformer has no non-sensitive
        record to persist ahead of enrichment (to_base_message returns None) —
        the raw OpsBot channel summary must never reach Scribe, only the
        Enricher's SQS message."""
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        payload = {
            "category": "incident_channel_summary",
            "source": "opsbot",
            "entity_id": "INC-1234",
            "generated_at": "2026-07-14T18:32:00Z",
            "data": {"summary": "Raw OpsBot channel summary text"},
        }
        event = _encode_callback_event(
            external_service_type="matik_generic_webhook_events",
            headers={},
            payload_json=json.dumps(payload),
        )
        msg = _make_kafka_msg(event, topic="yoyo.callback.matik_generic_webhook_events")
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        enricher.publish.assert_awaited_once()
        published = enricher.publish.await_args.args[0]
        assert published.content == {
            "incident_channel_summary": "Raw OpsBot channel summary text"
        }
        consumer.commit.assert_called_once_with(msg, asynchronous=False)


class TestHMACValidation:
    """Chronicler-side HMAC validation — Yoyo passes webhooks through unverified."""

    @pytest.mark.asyncio
    async def test_valid_signature_lets_event_through(self) -> None:
        secret = "shhhh"
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        cfg = ChroniclerConfig(github_webhook_secret=secret)
        loop, consumer = _make_loop(
            scribe=scribe, enricher=enricher, chronicler_config=cfg
        )

        msg = _make_kafka_msg(_make_callback_event(secret=secret))
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_invalid_signature_drops_event(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock())
        cfg = ChroniclerConfig(github_webhook_secret="right-secret")
        loop, consumer = _make_loop(
            scribe=scribe, enricher=enricher, chronicler_config=cfg
        )

        msg = _make_kafka_msg(_make_callback_event(secret="wrong-secret"))
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        enricher.publish.assert_not_awaited()
        # offset still committed — invalid signatures are permanent failures,
        # not transient, so a retry would just fail again.
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_missing_signature_header_drops_event(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        cfg = ChroniclerConfig(github_webhook_secret="shhhh")
        loop, consumer = _make_loop(scribe=scribe, chronicler_config=cfg)

        # No signature in headers at all.
        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_no_secret_configured_skips_validation(self) -> None:
        """Sandbox bootstrap: secret not yet wired -> let events through."""
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        # Default ChroniclerConfig has github_webhook_secret=None.
        loop, consumer = _make_loop(
            scribe=scribe, enricher=enricher, chronicler_config=ChroniclerConfig()
        )

        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    @pytest.mark.asyncio
    async def test_empty_secret_string_skips_validation(self) -> None:
        """An explicitly empty string is treated as 'unset' (matches None)."""
        scribe = MagicMock(send=AsyncMock())
        cfg = ChroniclerConfig(github_webhook_secret="")
        loop, _ = _make_loop(scribe=scribe, chronicler_config=cfg)

        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_webhook_secret_key_short_circuits(self) -> None:
        """Providers with webhook_secret_key='' (e.g. Jira) skip validation entirely.

        The empty key would cause getattr(config, "") to look up an unintended
        attribute. The consumer must short-circuit before that lookup runs.
        """
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        # Build a Jira issue_updated event for a TCMR key.
        jira_payload = {
            "webhookEvent": "jira:issue_updated",
            "timestamp": 1778600000000,
            "issue": {
                "id": "10042",
                "key": "TCMR-9001",
                "fields": {
                    "summary": "Bump proxysql pool",
                    "status": {"name": "Done"},
                    "created": "2026-05-14T09:30:00.000+0000",
                },
            },
        }
        blob = _encode_callback_event(
            external_service_type="matik_sandbox_jira_webhook_events",
            headers={},
            payload_json=json.dumps(jira_payload),
        )
        # No relevant secret on config — would normally be ignored anyway
        # since JiraTransformer.webhook_secret_key == "".
        loop, consumer = _make_loop(
            scribe=scribe, enricher=enricher, chronicler_config=ChroniclerConfig()
        )

        msg = _make_kafka_msg(
            blob, topic="yoyo.callback.matik_sandbox_jira_webhook_events"
        )
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    async def test_incidentio_event_publishes_to_both_queues(self) -> None:
        """End-to-end: a signed Incident.io created event flows to Scribe + Enricher."""
        from datetime import datetime
        from math import floor

        from svix.webhooks import Webhook

        secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))

        incident_payload = {
            "event_type": "public_incident.incident_created_v2",
            "public_incident.incident_created_v2": {
                "id": "01FDAG4SAP5TYPT98WGR2N7W91",
                "reference": "INC-123",
                "severity": {"name": "Minor"},
                "incident_status": {"name": "Investigating", "category": "active"},
                "slack_channel_id": "C02AW36C1M5",
                "visibility": "public",
                "name": "Database is sad",
                "summary": "Connection pool exhausted",
                "created_at": "2026-05-19T13:00:00Z",
                "updated_at": "2026-05-19T13:05:00Z",
                "incident_timestamp_values": [],
                "custom_field_entries": [],
            },
        }
        body_json = json.dumps(incident_payload)
        now = datetime.now(UTC)
        sig = Webhook(secret).sign("msg_incident_1", now, body_json)
        headers = {
            "webhook-id": "msg_incident_1",
            "webhook-timestamp": str(floor(now.timestamp())),
            "webhook-signature": sig,
        }
        blob = _encode_callback_event(
            external_service_type="matik_sandbox_incidentio_webhook_events",
            headers=headers,
            payload_json=body_json,
        )

        loop, consumer = _make_loop(
            scribe=scribe,
            enricher=enricher,
            chronicler_config=ChroniclerConfig(incidentio_webhook_secret=secret),
        )
        msg = _make_kafka_msg(
            blob, topic="yoyo.callback.matik_sandbox_incidentio_webhook_events"
        )
        await loop._handle_message(msg)

        scribe.send.assert_awaited_once()
        enricher.publish.assert_awaited_once()
        # Scribe payload carries the incident_id under data.
        scribe_call = scribe.send.await_args.args[0]
        assert scribe_call.source_type == "incidentio"
        assert scribe_call.data["incident_id"] == "01FDAG4SAP5TYPT98WGR2N7W91"
        # Enrichment request is keyed by the same id.
        enricher_call = enricher.publish.await_args.args[0]
        assert enricher_call.entity_id == {"incident_id": "01FDAG4SAP5TYPT98WGR2N7W91"}
        consumer.commit.assert_called_once_with(msg, asynchronous=False)

    async def test_incidentio_invalid_signature_drops_event(self) -> None:
        """Tampered Svix signature on Incident.io payload → no SQS publishes."""
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"

        body_json = json.dumps(
            {
                "event_type": "public_incident.incident_created_v2",
                "public_incident.incident_created_v2": {"id": "x", "reference": "y"},
            }
        )
        # Headers reference a signature that won't validate against this body.
        headers = {
            "webhook-id": "msg_bad",
            "webhook-timestamp": "1778600000",
            "webhook-signature": "v1,deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef0000=",
        }
        blob = _encode_callback_event(
            external_service_type="matik_sandbox_incidentio_webhook_events",
            headers=headers,
            payload_json=body_json,
        )
        loop, consumer = _make_loop(
            scribe=scribe,
            enricher=enricher,
            chronicler_config=ChroniclerConfig(incidentio_webhook_secret=secret),
        )
        msg = _make_kafka_msg(
            blob, topic="yoyo.callback.matik_sandbox_incidentio_webhook_events"
        )
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        enricher.publish.assert_not_awaited()
        consumer.commit.assert_called_once_with(msg, asynchronous=False)


class TestMetricsEmission:
    """Confirm the consumer fires the expected ChroniclerMetrics calls
    on each terminal branch. We use a MagicMock spec-bound to the real
    ChroniclerMetrics class so signature drift breaks these tests."""

    def _metrics(self) -> MagicMock:
        from common.metrics.chronicler_metrics import ChroniclerMetrics

        m = MagicMock(spec=ChroniclerMetrics)
        # start_message returns a recorder closure; use a MagicMock so we can
        # assert how it was invoked.
        m.start_message.return_value = MagicMock()
        return m

    async def test_success_path_records_received_processed_publishes(self) -> None:
        metrics = self._metrics()
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, _ = _make_loop(scribe=scribe, enricher=enricher, metrics=metrics)

        msg = _make_kafka_msg(_make_callback_event())
        await loop._handle_message(msg)

        metrics.record_received.assert_called_once()
        # Both publish targets recorded as success.
        publish_calls = [c.args for c in metrics.record_publish.call_args_list]
        assert ("scribe", "github", True) in publish_calls
        assert ("enricher", "github", True) in publish_calls
        # No signature scheme configured for tests → skipped_no_secret on github
        # (GHE has a scheme but no secret in test config).
        metrics.record_signature.assert_called_with("github", "skipped_no_secret")
        # start_message recorder fires once with success.
        metrics.start_message.return_value.assert_called_once_with("github", "success")

    async def test_filtered_event_records_filtered_status(self) -> None:
        metrics = self._metrics()
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock())
        loop, _ = _make_loop(scribe=scribe, enricher=enricher, metrics=metrics)

        msg = _make_kafka_msg(
            _make_callback_event(payload=_make_pr_payload(merged=False))
        )
        await loop._handle_message(msg)

        scribe.send.assert_not_awaited()
        metrics.start_message.return_value.assert_called_once_with("github", "filtered")

    async def test_unknown_provider_records_unknown_provider(self) -> None:
        metrics = self._metrics()
        loop, _ = _make_loop(metrics=metrics)
        msg = _make_kafka_msg(
            _make_callback_event(external_service_type="matik_unknown_events")
        )
        await loop._handle_message(msg)
        # Provider derived from external_service_type ("unknown").
        recorder = metrics.start_message.return_value
        recorder.assert_called_once()
        (_, status) = recorder.call_args.args
        assert status == "unknown_provider"

    async def test_malformed_envelope_records_malformed_status(self) -> None:
        metrics = self._metrics()
        loop, _ = _make_loop(metrics=metrics)
        msg = _make_kafka_msg(b"not a valid thrift envelope")
        await loop._handle_message(msg)
        metrics.start_message.return_value.assert_called_once_with(
            "unknown", "malformed_envelope"
        )

    async def test_empty_body_records_malformed_envelope(self) -> None:
        metrics = self._metrics()
        loop, _ = _make_loop(metrics=metrics)
        msg = _make_kafka_msg(None)
        await loop._handle_message(msg)
        metrics.start_message.return_value.assert_called_once_with(
            "unknown", "malformed_envelope"
        )

    async def test_invalid_signature_records_signature_failed(self) -> None:
        metrics = self._metrics()
        loop, _ = _make_loop(
            metrics=metrics,
            chronicler_config=ChroniclerConfig(github_webhook_secret="real-secret"),
        )
        msg = _make_kafka_msg(_make_callback_event(override_signature="sha256=bad"))
        await loop._handle_message(msg)

        metrics.record_signature.assert_called_with("github", "invalid")
        metrics.start_message.return_value.assert_called_once_with(
            "github", "signature_failed"
        )

    async def test_scribe_failure_records_failed_publish(self) -> None:
        metrics = self._metrics()
        scribe = MagicMock(send=AsyncMock(side_effect=RuntimeError("scribe down")))
        loop, _ = _make_loop(scribe=scribe, metrics=metrics)
        msg = _make_kafka_msg(_make_callback_event())

        with pytest.raises(RuntimeError):
            await loop._handle_message(msg)

        publish_calls = [c.args for c in metrics.record_publish.call_args_list]
        assert ("scribe", "github", False) in publish_calls
        # Terminal status from the start_message recorder is "error" since
        # the exception propagated through the try/finally.
        metrics.start_message.return_value.assert_called_once_with("unknown", "error")

    async def test_enricher_failure_records_failed_publish(self) -> None:
        metrics = self._metrics()
        scribe = MagicMock(send=AsyncMock())
        # publish() returning None triggers the RuntimeError branch.
        enricher = MagicMock(publish=AsyncMock(return_value=None))
        loop, _ = _make_loop(scribe=scribe, enricher=enricher, metrics=metrics)
        msg = _make_kafka_msg(_make_callback_event())

        with pytest.raises(RuntimeError):
            await loop._handle_message(msg)

        publish_calls = [c.args for c in metrics.record_publish.call_args_list]
        assert ("scribe", "github", True) in publish_calls
        assert ("enricher", "github", False) in publish_calls


class TestRequestShutdown:
    def test_sets_event(self) -> None:
        loop, _ = _make_loop()
        assert loop._shutdown.is_set() is False
        loop.request_shutdown()
        assert loop._shutdown.is_set() is True

    def test_is_idempotent(self) -> None:
        loop, _ = _make_loop()
        loop.request_shutdown()
        loop.request_shutdown()
        assert loop._shutdown.is_set() is True


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_processes_message_then_exits(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        enricher = MagicMock(publish=AsyncMock(return_value="msg-id"))
        loop, consumer = _make_loop(scribe=scribe, enricher=enricher)

        msg = _make_kafka_msg(_make_callback_event())
        poll_calls = {"n": 0}

        def fake_poll(_timeout: float) -> Any:
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                return msg
            loop.request_shutdown()
            return None

        consumer.poll.side_effect = fake_poll

        await loop.run()

        consumer.subscribe.assert_called_once_with(loop._kafka_config.topics)
        scribe.send.assert_awaited_once()
        consumer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_none_polls(self) -> None:
        loop, consumer = _make_loop()
        poll_calls = {"n": 0}

        def fake_poll(_timeout: float) -> Any:
            poll_calls["n"] += 1
            if poll_calls["n"] >= 2:
                loop.request_shutdown()
            return None

        consumer.poll.side_effect = fake_poll
        await loop.run()
        consumer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_messages_with_errors(self) -> None:
        scribe = MagicMock(send=AsyncMock())
        loop, consumer = _make_loop(scribe=scribe)

        err_msg = _make_kafka_msg(b"x", error=object())

        def fake_poll(_timeout: float) -> Any:
            loop.request_shutdown()
            return err_msg

        consumer.poll.side_effect = fake_poll
        await loop.run()

        scribe.send.assert_not_awaited()
        consumer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_called_even_on_exception(self) -> None:
        loop, consumer = _make_loop()
        consumer.poll.side_effect = RuntimeError("kafka dead")

        with pytest.raises(RuntimeError, match="kafka dead"):
            await loop.run()

        consumer.close.assert_called_once()


class TestBuildConsumer:
    def test_passes_config_to_confluent_consumer(self) -> None:
        kafka_config = ChroniclerKafkaConfig(
            bootstrap_servers="kafka-staging.kafka-staging:19092",
            group_id="matik-chronicler-test",
            auto_offset_reset="earliest",
        )
        fake_consumer = MagicMock()
        with patch("confluent_kafka.Consumer", return_value=fake_consumer) as ctor:
            result = build_consumer(kafka_config)

        assert result is fake_consumer
        ctor.assert_called_once_with(
            {
                "bootstrap.servers": "kafka-staging.kafka-staging:19092",
                "group.id": "matik-chronicler-test",
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )


class TestInstallSignalHandlers:
    @pytest.mark.asyncio
    async def test_binds_sigterm_and_sigint(self) -> None:
        loop_obj, _ = _make_loop()
        fake_asyncio_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=fake_asyncio_loop):
            install_signal_handlers(loop_obj)

        signals_bound = {
            call.args[0] for call in fake_asyncio_loop.add_signal_handler.call_args_list
        }
        assert signals_bound == {signal.SIGTERM, signal.SIGINT}
        # Each handler invokes loop.request_shutdown.
        for call in fake_asyncio_loop.add_signal_handler.call_args_list:
            assert call.args[1] == loop_obj.request_shutdown
