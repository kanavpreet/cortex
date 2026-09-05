"""OpenTelemetry span instrumentation for Bedrock Converse calls.

BedrockClient calls the Converse proxy over raw httpx, which has no OpenTelemetry
auto-instrumentor (unlike the OpenAI SDK that Facade uses), so spans are created
by hand here with GenAI semantic-convention attributes. Spans are emitted on the
tracer provider that LLMTracingClient.start() configures, and are a cheap no-op when
tracing is disabled.

A call is bracketed by two functions: traced_bedrock_call() opens the span and
records the request (model, operation, prompt content); record_bedrock_response()
records the response (model, token usage, output content).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager

from genai_studio.tracking.attrs import ATTR_BRAINTRUST_SPAN_TYPE
from opentelemetry import trace
from opentelemetry.semconv._incubating.attributes.gen_ai_attributes import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from common.llm_tracing.content import should_capture_content

__all__ = ["record_bedrock_response", "traced_bedrock_call"]

_GEN_AI_SYSTEM_BEDROCK = "aws.bedrock"

_tracer = trace.get_tracer(__name__)


def _to_parts_messages(messages: list[dict[str, str]]) -> list[dict[str, object]]:
    """Convert role/content messages to the GenAI ``parts`` shape.

    Braintrust lifts messages into its Input/Output columns only in this
    structured form (``{"role", "parts": [{"content", "type": "text"}]}``), which
    is what the OpenAI auto-instrumentor emits for Facade spans.
    """
    return [
        {"role": m["role"], "parts": [{"content": m["content"], "type": "text"}]}
        for m in messages
    ]


@contextmanager
def traced_bedrock_call(
    model: str,
    operation: str,
    input_messages: list[dict[str, str]] | None = None,
) -> Iterator[Span]:
    """Open a span for a Bedrock Converse call and record the request.

    Sets the request attributes (system, operation, model) and — when content
    capture is enabled and ``input_messages`` is given — the prompt content under
    ``gen_ai.input.messages``. Yields the span so the caller records the response
    via ``record_bedrock_response()``. Marks the span an error and re-raises if
    the wrapped call fails.

    Args:
        model: The Bedrock model ID being called.
        operation: Operation label (e.g. "assign_correlations"), matching the
            `operation` label used elsewhere for Facade/Bedrock metrics.
        input_messages: Request messages as ``[{"role", "content"}, ...]`` for
            content capture; omit to trace metadata only.
    """
    with _tracer.start_as_current_span("bedrock.chat", kind=SpanKind.CLIENT) as span:
        # The OpenAI auto-instrumentor sets this on Facade spans itself; Bedrock's
        # hand-rolled span needs it explicitly or Braintrust won't classify it as
        # an LLM span and its token usage won't roll up into Braintrust's metrics.
        span.set_attribute(ATTR_BRAINTRUST_SPAN_TYPE, "llm")
        span.set_attribute(GEN_AI_SYSTEM, _GEN_AI_SYSTEM_BEDROCK)
        span.set_attribute(GEN_AI_OPERATION_NAME, operation)
        span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        if input_messages is not None and should_capture_content():
            span.set_attribute(
                GEN_AI_INPUT_MESSAGES, json.dumps(_to_parts_messages(input_messages))
            )
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def record_bedrock_response(
    span: Span,
    response_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    output_text: str,
) -> None:
    """Record the response model, token usage, and (if enabled) output content.

    The output content uses ``gen_ai.output.messages`` — the same key the Facade
    (OpenAI) auto-instrumentor emits — so a dataset-from-logs reads Bedrock and
    Facade traces uniformly. Output content is skipped when content capture is
    disabled. It is only safe on the span because tracing runs in GENAI mode
    (Braintrust-only), so it never reaches AirTrace.

    Args:
        span: The span yielded by traced_bedrock_call().
        response_model: The model ID that actually served the request.
        prompt_tokens: Input token count from the Converse response.
        completion_tokens: Output token count from the Converse response.
        output_text: The assistant response text.
    """
    span.set_attribute(GEN_AI_RESPONSE_MODEL, response_model)
    span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens)
    span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, completion_tokens)
    if should_capture_content():
        span.set_attribute(
            GEN_AI_OUTPUT_MESSAGES,
            json.dumps(
                [
                    {
                        "role": "assistant",
                        "parts": [{"content": output_text, "type": "text"}],
                        "finish_reason": "stop",
                    }
                ]
            ),
        )
