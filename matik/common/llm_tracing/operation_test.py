"""Unit tests for the LLM-operation wrapper span."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import common.llm_tracing.operation as operation_mod
from common.llm_tracing.operation import traced_llm_operation


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(operation_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


class TestTracedLLMOperation:
    def test_names_span_and_sets_metadata_and_tags(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """Span is named by operation; source/operation are metadata + tags."""
        with traced_llm_operation("root_cause_summary", source="incidentio"):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.name == "root_cause_summary"
        assert span.attributes is not None
        assert span.attributes["operation"] == "root_cause_summary"
        assert span.attributes["source"] == "incidentio"
        assert span.attributes["braintrust.tags"] == (
            "operation:root_cause_summary",
            "source:incidentio",
        )

    def test_source_optional(self, exporter: InMemorySpanExporter) -> None:
        """Without a source, only the operation tag is set."""
        with traced_llm_operation("incident_correlation"):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.name == "incident_correlation"
        assert span.attributes is not None
        assert "source" not in span.attributes
        assert span.attributes["braintrust.tags"] == ("operation:incident_correlation",)

    def test_entity_id_sets_attributes_and_tags(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """entity_id keys become their own span attributes and tags."""
        with traced_llm_operation(
            "incident_correlation",
            source="correlation",
            entity_id={"incident_id": "INC-99"},
        ):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.attributes is not None
        assert span.attributes["incident_id"] == "INC-99"
        assert span.attributes["braintrust.tags"] == (
            "operation:incident_correlation",
            "source:correlation",
            "incident_id:INC-99",
        )

    def test_entity_id_optional(self, exporter: InMemorySpanExporter) -> None:
        """Without entity_id, no extra attributes or tags are added."""
        with traced_llm_operation("root_cause_summary", source="incidentio"):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.attributes is not None
        assert "incident_id" not in span.attributes
        assert span.attributes["braintrust.tags"] == (
            "operation:root_cause_summary",
            "source:incidentio",
        )

    def test_entity_created_at_sets_attribute_but_not_tag(
        self, exporter: InMemorySpanExporter
    ) -> None:
        """entity_created_at becomes an attribute only, never a tag."""
        with traced_llm_operation(
            "incident_correlation",
            source="correlation",
            entity_id={"incident_id": "INC-99"},
            entity_created_at=datetime(2026, 7, 20, 14, 32, 0),
        ):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.attributes is not None
        assert span.attributes["entity_created_at"] == "2026-07-20T14:32:00"
        assert span.attributes["braintrust.tags"] == (
            "operation:incident_correlation",
            "source:correlation",
            "incident_id:INC-99",
        )

    def test_entity_created_at_optional(self, exporter: InMemorySpanExporter) -> None:
        """Without entity_created_at, no extra attribute is added."""
        with traced_llm_operation("incident_correlation"):
            pass

        (span,) = exporter.get_finished_spans()
        assert span.attributes is not None
        assert "entity_created_at" not in span.attributes
