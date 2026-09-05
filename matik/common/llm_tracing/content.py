"""Toggle for capturing prompt/response CONTENT on LLM spans.

Prompt and response text can carry PII, so whether it's captured is switchable.
The Facade (OpenAI) instrumentor reads the ``TRACELOOP_TRACE_CONTENT`` env var to
decide whether to log message content, and the manual Bedrock spans check it too,
so this single variable controls content capture across both providers. Content
is only safe to capture because tracing runs in GENAI mode (Braintrust-only), so
it never reaches AirTrace.
"""

import os

_TRACELOOP_TRACE_CONTENT_ENV = "TRACELOOP_TRACE_CONTENT"

__all__ = ["set_capture_content", "should_capture_content"]


def should_capture_content() -> bool:
    """Whether prompt/response content should be logged on spans."""
    return os.getenv(_TRACELOOP_TRACE_CONTENT_ENV, "true").strip().lower() == "true"


def set_capture_content(enabled: bool) -> None:
    """Set the content-capture toggle for both the Facade and Bedrock paths."""
    os.environ[_TRACELOOP_TRACE_CONTENT_ENV] = "true" if enabled else "false"
