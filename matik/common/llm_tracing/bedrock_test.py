"""Unit tests for Bedrock manual span instrumentation."""

import json
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import common.llm_tracing.bedrock as bedrock_tracing
from common.llm_tracing.bedrock import (
    record_bedrock_response,
    traced_bedrock_call,
)


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """Point the module's tracer at a fresh in-memory exporter for this test.

    The OTel SDK only allows the *global* TracerProvider to be set once per
    process, so tests can't each call set_tracer_provider(). Instead, build a
    standalone provider per test and monkeypatch the module-level `_tracer`
    that traced_bedrock_call() uses, bypassing the global singleton entirely.
    """
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(bedrock_tracing, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


class TestTracedBedrockCall:
    """Test suite for traced_bedrock_call and record_bedrock_response."""

    def test_records_request_attributes(self, exporter: InMemorySpanExporter) -> None:
        """Test that the span records model/operation/system attributes."""
        with traced_bedrock_call("anthropic.claude", "assign_correlations") as span:
            assert span.is_recording()

        (finished_span,) = exporter.get_finished_spans()
        assert finished_span.name == "bedrock.chat"
        assert finished_span.attributes is not None
        assert finished_span.attributes["gen_ai.system"] == "aws.bedrock"
        assert finished_span.attributes["gen_ai.operation.name"] == (
            "assign_correlations"
        )
        assert finished_span.attributes["gen_ai.request.model"] == "anthropic.claude"
        assert finished_span.attributes["braintrust.span_attributes.type"] == "llm"

    def test_record_response_sets_model_tokens_and_output(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """record_bedrock_response attaches response model, tokens, and output."""
        with traced_bedrock_call("anthropic.claude", "default") as span:
            record_bedrock_response(span, "anthropic.claude-v2", 10, 5, "the answer")

        (finished_span,) = exporter.get_finished_spans()
        assert finished_span.attributes is not None
        assert finished_span.attributes["gen_ai.response.model"] == (
            "anthropic.claude-v2"
        )
        assert finished_span.attributes["gen_ai.usage.input_tokens"] == 10
        assert finished_span.attributes["gen_ai.usage.output_tokens"] == 5
        assert json.loads(str(finished_span.attributes["gen_ai.output.messages"])) == [
            {
                "role": "assistant",
                "parts": [{"content": "the answer", "type": "text"}],
                "finish_reason": "stop",
            }
        ]

    def test_captures_input_and_output_content(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """Prompt + response content log under the GenAI message keys (as Facade)."""
        messages = [{"role": "user", "content": "why did it fail?"}]
        with traced_bedrock_call("anthropic.claude", "default", messages) as span:
            record_bedrock_response(
                span, "anthropic.claude", 1, 1, "a partition change"
            )

        (finished_span,) = exporter.get_finished_spans()
        assert finished_span.attributes is not None
        assert json.loads(str(finished_span.attributes["gen_ai.input.messages"])) == [
            {"role": "user", "parts": [{"content": "why did it fail?", "type": "text"}]}
        ]
        assert json.loads(str(finished_span.attributes["gen_ai.output.messages"])) == [
            {
                "role": "assistant",
                "parts": [{"content": "a partition change", "type": "text"}],
                "finish_reason": "stop",
            }
        ]

    def test_content_omitted_when_capture_disabled(
        self, exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With content capture off, no prompt/response content lands on the span."""
        monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "false")
        messages = [{"role": "user", "content": "secret"}]
        with traced_bedrock_call("anthropic.claude", "default", messages) as span:
            record_bedrock_response(span, "anthropic.claude", 1, 1, "out")

        (finished_span,) = exporter.get_finished_spans()
        assert finished_span.attributes is not None
        assert "gen_ai.input.messages" not in finished_span.attributes
        assert "gen_ai.output.messages" not in finished_span.attributes

    def test_marks_span_as_error_and_reraises(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """Test that an exception inside the block is recorded and re-raised."""
        with (
            pytest.raises(RuntimeError, match="boom"),
            traced_bedrock_call("anthropic.claude", "default"),
        ):
            raise RuntimeError("boom")

        (finished_span,) = exporter.get_finished_spans()
        assert finished_span.status.status_code.name == "ERROR"
