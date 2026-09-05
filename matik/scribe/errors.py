"""Scribe service error taxonomy."""


class NonRetryableError(Exception):
    """Errors that should go directly to DLQ without retry."""


class MalformedMessageError(NonRetryableError):
    """Message failed JSON parsing, routing, or Pydantic validation."""


class BaseRecordNotFoundError(Exception):
    """Enrichment arrived before its base record exists in the database.

    Triggers a long-wait visibility timeout (configurable via
    ScribeConfig.enrichment_base_not_found_delay) so the message stays in the
    queue and is retried once the base record has been written. This is distinct
    from a transient DB error (RuntimeError), which uses the short exponential
    backoff (10 / 30 / 60s).
    """
