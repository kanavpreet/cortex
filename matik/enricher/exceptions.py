# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class NonRetryableError(Exception):
    """Base class for errors that must not be retried.

    Messages raising these errors are routed directly to the DLQ.
    """


class MalformedMessageError(NonRetryableError):
    """Raised when a message cannot be parsed or fails schema validation.

    These messages will never succeed on retry — send to DLQ immediately.
    """


class ContentFilteredError(NonRetryableError):
    """Raised when the Facade LLM blocked the request via content filtering.

    The same prompt will always be filtered, so retrying is pointless.
    """
