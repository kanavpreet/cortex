"""Generic Matik webhook transformer (OpsBot on-demand feed).

Unlike the other transformers, this is a webhook Matik originates the
contract for, not a third-party vendor's — OpsBot posts to it directly via
Yoyo's usual callback pathway. It's deliberately generic: a `category` field
in the payload discriminates sub-use-cases so future ones can share this same
provider registration, signature scheme, and transformer without a new
onboarding round-trip.

Scope (V1):
    Accepted category:
        - incident_channel_summary — OpsBot's raw Slack-channel summary draft
          for an in-flight incident. See the design doc:
          https://jira.airbnb.biz/browse/ITE-797334

Signature scheme is raw HMAC-SHA256 (mirrors the GitHub transformer's
`X-Hub-Signature-256` pattern), not Incident.io's Svix-based scheme — this is
a webhook we originate ourselves, so there's no reason to inherit another
vendor's delivery convention. The shared secret is sourced from secret-lair
via `ChroniclerConfig.generic_webhook_secret` (secret-lair key
`chronicler.generic_webhook_secret`).

Registered Yoyo external_service_types (see the #api-infra registration):
    - matik_generic_webhook_events         (production; staging shares this)
    - matik_sandbox_generic_webhook_events (sandbox)
Both tokenize to provider "matik" via the "generic" token — see
chronicler/models/callback_event.py.

Payload envelope (proposed, pending @raheel sign-off — see the design doc):
    {
      "category": "incident_channel_summary",
      "source": "opsbot",
      "entity_id": "INC-1234",
      "generated_at": "2026-07-14T18:32:00Z",
      "data": {"summary": "<single-paragraph LLM-generated summary>"}
    }
"""

import hashlib
import hmac
from typing import Any

from pydantic import BaseModel

from chronicler.transformers.base import register_transformer
from common.models.enricher_messages import EnrichmentRequest
from common.utils.datetime_utils import utc_now_naive

MATIK_SIGNATURE_HEADER = "X-Matik-Signature-256"
_SIGNATURE_PREFIX = "sha256="

# Categories this transformer accepts. Each maps 1:1 to a Scribe source_type;
# a future category would extend this set rather than requiring a new
# provider/transformer registration.
_CATEGORY_INCIDENT_CHANNEL_SUMMARY = "incident_channel_summary"
_ACCEPTED_CATEGORIES = frozenset({_CATEGORY_INCIDENT_CHANNEL_SUMMARY})


def _get_header_ci(headers: dict[str, str], key: str) -> str:
    """Case-insensitive header lookup. Yoyo lowercases header keys."""
    if key in headers:
        return headers[key]
    lower = key.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return ""


class MatikWebhookTransformer:
    """Transforms generic Matik/OpsBot webhook payloads into Scribe and
    Enricher messages, routed by the payload's `category` field."""

    # V1 supports a single category; source_type reflects it directly. If a
    # second category is added, this must become category-derived instead of
    # a fixed class attribute.
    source_type: str = _CATEGORY_INCIDENT_CHANNEL_SUMMARY
    # Category lives in the payload body, not an HTTP header.
    event_type_header: str = ""
    webhook_secret_key: str = "generic_webhook_secret"

    def validate_signature(
        self, body: bytes, headers: dict[str, str], secret: str
    ) -> bool:
        """HMAC-SHA256 over the raw body, hex-encoded, prefixed with `sha256=`.

        Same construction as the GitHub transformer's `X-Hub-Signature-256`.
        Yoyo lowercases header keys before producing to Kafka, so we look up
        case-insensitively.
        """
        signature_header = _get_header_ci(headers, MATIK_SIGNATURE_HEADER)
        if not signature_header.startswith(_SIGNATURE_PREFIX):
            return False
        expected = (
            _SIGNATURE_PREFIX
            + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(signature_header, expected)

    def should_process(self, event_type: str, payload: dict[str, Any]) -> bool:
        return payload.get("category") in _ACCEPTED_CATEGORIES

    def to_base_message(self, payload: dict[str, Any]) -> BaseModel | None:
        """No base write for this source.

        The entire payload is the raw incident-channel summary text itself —
        unlike incident.io/JIRA/GHE PR, there's no structured, non-sensitive
        metadata to persist ahead of enrichment. The raw text is never
        written to the DB; it only ever reaches the Enricher via
        `to_enrichment_request`'s SQS message. `chronicler.consumer` treats a
        `None` return as "skip the Scribe base write for this event."
        """
        return None

    def to_enrichment_request(
        self, payload: dict[str, Any], task_id: str
    ) -> EnrichmentRequest | None:
        summary = payload.get("data", {}).get("summary")
        entity_id = payload.get("entity_id")
        if not summary or not entity_id:
            return None

        return EnrichmentRequest(
            source_type=self.source_type,
            producer="chronicler",
            task_id=task_id,
            entity_id={"reference_id": entity_id},
            content={"incident_channel_summary": summary},
            entered_at=utc_now_naive(),
        )


register_transformer("matik", MatikWebhookTransformer())
