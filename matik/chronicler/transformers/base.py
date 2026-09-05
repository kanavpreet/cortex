"""Provider transformer protocol and registry.

Each webhook provider (GitHub, JIRA, Incident.io) implements the
ProviderTransformer protocol and registers itself in the global registry.
The Kafka consumer loop dispatches to the appropriate transformer by topic.

Yoyo does NOT validate HMAC signatures — Chronicler must. Each transformer
declares the provider-specific signature scheme via `validate_signature`,
and `webhook_secret_key` tells the consumer which `ChroniclerConfig` field
holds the matching shared secret.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from common.models.enricher_messages import EnrichmentRequest


@runtime_checkable
class ProviderTransformer(Protocol):
    """Interface that each webhook provider transformer must implement."""

    source_type: str
    event_type_header: str
    webhook_secret_key: (
        str  # attribute name on ChroniclerConfig that holds the HMAC secret
    )

    def validate_signature(
        self, body: bytes, headers: dict[str, str], secret: str
    ) -> bool:
        """Verify the provider-specific signature header against the raw body.

        Args:
            body: Original webhook body bytes as the provider sent them.
            headers: HTTP headers from the provider (lowercased by Yoyo).
            secret: Shared HMAC secret.

        Returns:
            True if the signature is valid, False otherwise.
        """
        ...

    def should_process(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Return True if this event should be processed.

        Args:
            event_type: Provider-specific event type (e.g., "pull_request").
            payload: Parsed webhook JSON payload.
        """
        ...

    def to_base_message(self, payload: dict[str, Any]) -> BaseModel | None:
        """Transform a webhook payload into a sanitized Scribe base message.

        Sensitive fields (PR body, JIRA description) must be stripped from
        the returned message — they are only included in the enrichment request.

        Args:
            payload: Parsed webhook JSON payload.

        Returns:
            A Pydantic model suitable for SQSPublisher.send(), or None if the
            source has no non-sensitive record to persist ahead of enrichment
            (e.g. a payload whose entire content is sensitive raw text) — the
            consumer skips the Scribe base write in that case.
        """
        ...

    def to_enrichment_request(
        self, payload: dict[str, Any], task_id: str
    ) -> EnrichmentRequest | None:
        """Extract LLM-summarizable fields for enrichment.

        Args:
            payload: Parsed webhook JSON payload.
            task_id: Unique task ID for log correlation.

        Returns:
            An EnrichmentRequest, or None if no enrichment is needed.
        """
        ...


_PROVIDER_REGISTRY: dict[str, ProviderTransformer] = {}


def register_transformer(provider: str, transformer: ProviderTransformer) -> None:
    """Register a provider transformer in the global registry.

    Args:
        provider: Provider name used as the URL path param (e.g., "github").
        transformer: Transformer instance implementing ProviderTransformer.
    """
    _PROVIDER_REGISTRY[provider] = transformer


def get_transformer(provider: str) -> ProviderTransformer:
    """Look up a registered provider transformer.

    Args:
        provider: Provider name (e.g., "github").

    Returns:
        The registered ProviderTransformer instance.

    Raises:
        KeyError: If no transformer is registered for the provider.
    """
    return _PROVIDER_REGISTRY[provider]
