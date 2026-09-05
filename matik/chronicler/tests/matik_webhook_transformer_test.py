"""Tests for the generic Matik webhook transformer (OpsBot on-demand feed)."""

import hashlib
import hmac

from chronicler.transformers.matik_webhook import (
    MATIK_SIGNATURE_HEADER,
    MatikWebhookTransformer,
)


def _make_webhook_payload(
    category: str = "incident_channel_summary",
    entity_id: str = "INC-1234",
    summary: str | None = "Raw OpsBot channel summary text",
) -> dict[str, object]:
    """Build a realistic Matik/OpsBot webhook payload."""
    payload: dict[str, object] = {
        "category": category,
        "source": "opsbot",
        "entity_id": entity_id,
        "generated_at": "2026-07-14T18:32:00Z",
        "data": {},
    }
    if summary is not None:
        payload["data"] = {"summary": summary}
    return payload


class TestShouldProcess:
    def test_incident_channel_summary_category_is_processed(self) -> None:
        t = MatikWebhookTransformer()
        assert t.should_process("", _make_webhook_payload()) is True

    def test_unknown_category_is_filtered(self) -> None:
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload(category="something_else")
        assert t.should_process("", payload) is False

    def test_missing_category_is_filtered(self) -> None:
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload()
        del payload["category"]
        assert t.should_process("", payload) is False


class TestToBaseMessage:
    def test_returns_none(self) -> None:
        """No non-sensitive record to persist ahead of enrichment — the raw
        channel summary is never written to the DB, only forwarded via
        to_enrichment_request's SQS message."""
        t = MatikWebhookTransformer()
        assert t.to_base_message(_make_webhook_payload()) is None

    def test_returns_none_even_for_malformed_payload(self) -> None:
        """Nothing is persisted here, so there's no reference_id/summary to
        validate — validation for a malformed payload happens in
        to_enrichment_request instead."""
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload(summary=None)
        del payload["entity_id"]
        assert t.to_base_message(payload) is None


class TestToEnrichmentRequest:
    def test_enrichment_request_has_correct_fields(self) -> None:
        t = MatikWebhookTransformer()
        request = t.to_enrichment_request(_make_webhook_payload(), task_id="task-1")
        assert request is not None
        assert request.source_type == "incident_channel_summary"
        assert request.producer == "chronicler"
        assert request.task_id == "task-1"
        assert request.entity_id == {"reference_id": "INC-1234"}
        assert request.content == {
            "incident_channel_summary": "Raw OpsBot channel summary text"
        }

    def test_empty_summary_returns_none(self) -> None:
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload()
        payload["data"] = {"summary": ""}
        assert t.to_enrichment_request(payload, task_id="task-1") is None

    def test_missing_data_key_returns_none(self) -> None:
        """A malformed payload missing the whole `data` object must not raise
        — an uncaught exception here skips the Kafka commit and the message
        retries forever."""
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload()
        del payload["data"]
        assert t.to_enrichment_request(payload, task_id="task-1") is None

    def test_missing_entity_id_returns_none(self) -> None:
        t = MatikWebhookTransformer()
        payload = _make_webhook_payload()
        del payload["entity_id"]
        assert t.to_enrichment_request(payload, task_id="task-1") is None


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestValidateSignature:
    def test_valid_signature(self) -> None:
        t = MatikWebhookTransformer()
        body = b'{"category": "incident_channel_summary"}'
        secret = "shhhh"
        headers = {MATIK_SIGNATURE_HEADER.lower(): _sign(body, secret)}
        assert t.validate_signature(body, headers, secret) is True

    def test_signature_canonical_case_header(self) -> None:
        t = MatikWebhookTransformer()
        body = b'{"category": "incident_channel_summary"}'
        secret = "shhhh"
        headers = {MATIK_SIGNATURE_HEADER: _sign(body, secret)}
        assert t.validate_signature(body, headers, secret) is True

    def test_mismatched_secret_fails(self) -> None:
        t = MatikWebhookTransformer()
        body = b'{"category": "incident_channel_summary"}'
        headers = {MATIK_SIGNATURE_HEADER.lower(): _sign(body, "wrong-secret")}
        assert t.validate_signature(body, headers, "right-secret") is False

    def test_tampered_body_fails(self) -> None:
        t = MatikWebhookTransformer()
        secret = "shhhh"
        sig_for_original = _sign(b'{"category": "incident_channel_summary"}', secret)
        headers = {MATIK_SIGNATURE_HEADER.lower(): sig_for_original}
        assert (
            t.validate_signature(b'{"category": "tampered"}', headers, secret) is False
        )

    def test_missing_signature_header_fails(self) -> None:
        t = MatikWebhookTransformer()
        assert t.validate_signature(b"body", {}, "secret") is False

    def test_wrong_prefix_fails(self) -> None:
        t = MatikWebhookTransformer()
        headers = {MATIK_SIGNATURE_HEADER.lower(): "md5=something"}
        assert t.validate_signature(b"body", headers, "secret") is False
