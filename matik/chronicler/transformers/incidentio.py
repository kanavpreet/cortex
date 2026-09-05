"""Incident.io webhook transformer.

Maps Incident.io public-incident webhook payloads (delivered via Yoyo's
Kafka topic, signed by Svix) onto Matik's `IncidentIOBaseMessage` for Scribe
and `EnrichmentRequest` for the Enricher's LLM-summarization pipeline.

Scope (V1):
    Accepted event_types:
        - public_incident.incident_created_v2
        - public_incident.incident_updated_v2
    Out of scope (logged and dropped):
        - public_incident.incident_status_changed_v2, *.closed_v2, etc.
          The historian's next crawl picks up terminal lifecycle state.

Signature verification uses the official Svix Python SDK so we stay aligned
with Svix protocol changes (header prefix migrations, signature versions,
replay tolerance) for free. The endpoint secret (whsec_...) is sourced from
secret-lair via `ChroniclerConfig.incidentio_webhook_secret`.
"""

from typing import Any

from svix.webhooks import Webhook, WebhookVerificationError

from chronicler.transformers.base import register_transformer
from common.models.enricher_messages import EnrichmentRequest
from common.models.scribe_messages import IncidentIOBaseMessage
from common.utils.datetime_utils import utc_now_naive
from common.utils.incidentio_utils import build_incident_from_payload

# Event types this transformer accepts. Other public_incident.*_v2 events are
# dropped; the historian's crawl backstops terminal-state updates.
_FULL_PIPELINE_EVENTS = frozenset(
    {
        "public_incident.incident_created_v2",
        "public_incident.incident_updated_v2",
    }
)


def _extract_incident(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the inner incident object out of the webhook envelope.

    Incident.io packages the incident object under a key matching the
    `event_type` value, e.g.
        payload["public_incident.incident_created_v2"] = {<incident>}.
    """
    event_type = payload.get("event_type", "")
    inner = payload.get(event_type)
    return inner if isinstance(inner, dict) else {}


class IncidentIOTransformer:
    """Transforms Incident.io webhook payloads into Scribe and Enricher messages."""

    source_type: str = "incidentio"
    # Event type lives in the payload body (`event_type`), not an HTTP header.
    event_type_header: str = ""
    webhook_secret_key: str = "incidentio_webhook_secret"

    def validate_signature(
        self, body: bytes, headers: dict[str, str], secret: str
    ) -> bool:
        """Verify the Svix signature via the official SDK.

        Svix's Webhook.verify reads `webhook-id` / `webhook-timestamp` /
        `webhook-signature` headers (or the legacy `svix-*` aliases), checks
        the signature against the whsec_-prefixed secret, and enforces a
        replay-protection window. We delegate entirely so we don't drift
        from the protocol.
        """
        try:
            Webhook(secret).verify(body, headers)
            return True
        except WebhookVerificationError:
            return False

    def should_process(self, event_type: str, payload: dict[str, Any]) -> bool:
        return payload.get("event_type", "") in _FULL_PIPELINE_EVENTS

    def to_base_message(self, payload: dict[str, Any]) -> IncidentIOBaseMessage:
        raw = _extract_incident(payload)
        incident, _name, _summary, _resolution_statement = build_incident_from_payload(
            raw
        )

        return IncidentIOBaseMessage(
            data=incident.model_dump(mode="json", exclude={"id"}),
            entered_at=utc_now_naive(),
        )

    def to_enrichment_request(
        self, payload: dict[str, Any], task_id: str
    ) -> EnrichmentRequest | None:
        raw = _extract_incident(payload)
        incident, name, summary, resolution_statement = build_incident_from_payload(raw)

        content: dict[str, str] = {}
        if name:
            content["name"] = name
        if summary:
            content["summary"] = summary
        if resolution_statement:
            content["resolution_statement"] = resolution_statement

        if not content:
            return None

        if incident.reference_id:
            content["reference_id"] = incident.reference_id

        return EnrichmentRequest(
            source_type=self.source_type,
            producer="chronicler",
            task_id=task_id,
            entity_id={"incident_id": raw.get("id", "")},
            content=content,
            entered_at=utc_now_naive(),
        )


register_transformer("incidentio", IncidentIOTransformer())
