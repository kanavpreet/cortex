"""Unit tests for EnrichmentHandler."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import enricher.handlers.enrichment_handler as enrichment_handler_mod
from common.clients.facade_client import (
    FacadeBadRequestError,
    FacadeContentFilteredError,
)
from common.models.enricher_config import EnricherConfig, SourceMappingEntry
from common.models.enricher_messages import EnrichmentRequest
from common.utils.hash_utils import generate_string_hash
from enricher.exceptions import ContentFilteredError
from enricher.handlers.enrichment_handler import (
    EnrichmentHandler,
    _canonical_entity_key,
)
from enricher.hash_cache import HashCache


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of enrichment_handler._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(
        enrichment_handler_mod, "_tracer", provider.get_tracer(__name__)
    )
    yield memory_exporter


def _make_config(mappings: dict[str, Any] | None = None) -> EnricherConfig:
    """Create an EnricherConfig with given source_mappings.

    Args:
        mappings: source_mappings dict; defaults to incidentio with two entries.
    """
    if mappings is None:
        mappings = {
            "incidentio": [
                SourceMappingEntry(
                    input_keys=["summary", "resolution_statement"],
                    output_field="root_cause_summary",
                    prompt="You are a root cause analyst.",
                    hash_field="root_cause_summary_hash",
                ),
                SourceMappingEntry(
                    input_keys=["name", "summary"],
                    output_field="description_summary",
                    prompt="You are a description summarizer.",
                    hash_field="description_hash",
                ),
            ]
        }
    return EnricherConfig(
        enricher_queue_url="https://sqs.example.com/enricher",
        enricher_dlq_url="https://sqs.example.com/dlq",
        scribe_llm_queue_url="https://sqs.example.com/scribe",
        general_prompt="General: {source_instructions}",
        source_mappings=mappings,
    )


def _make_request(
    source_type: str = "incidentio",
    content: dict[str, Any] | None = None,
    entered_at: "datetime | None" = None,
) -> EnrichmentRequest:
    """Create an EnrichmentRequest for testing.

    Args:
        source_type: The source type for the request.
        content: Content dict; defaults to summary + resolution_statement.
        entered_at: DLQ retry staleness guard timestamp (ADR 024); None by
            default (requests that predate the guard).
    """
    return EnrichmentRequest(
        source_type=source_type,
        producer="historian",
        task_id="task-test-001",
        entity_id={"incident_id": "INC-42"},
        content=content
        or {
            "name": "DB connection pool exhausted",
            "summary": "Database connection pool exhausted",
            "resolution_statement": "Increased pool size and restarted service",
        },
        entered_at=entered_at,
    )


def _make_api_client_mock(existing_hashes: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock MatikApiClient that returns given existing hashes."""
    mock = MagicMock()
    response = json.dumps(existing_hashes or {}).encode()
    mock.post_json_request = MagicMock(return_value=response)
    return mock


class TestEnrichmentHandlerHappyPath:
    """Tests for successful enrichment processing."""

    async def test_two_mappings_both_called(self) -> None:
        """Both mapping entries are processed and Facade is called twice."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=["Root cause: DB overload", "Resolution: Restarted"]
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, _make_config())
        await handler.enrich(_make_request())

        assert facade.send_message_with_retry.await_count == 2

    async def test_opens_named_span_when_fetching_existing_hashes(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """enrich() opens a fetch_existing_hashes span when no hashes are pre-fetched."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=["Root cause text", "Resolution text"]
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()
        api_client = _make_api_client_mock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=api_client
        )
        await handler.enrich(_make_request())

        span_names = [s.name for s in span_exporter.get_finished_spans()]
        assert "fetch_existing_hashes" in span_names
        span = next(
            s
            for s in span_exporter.get_finished_spans()
            if s.name == "fetch_existing_hashes"
        )
        assert span.attributes is not None
        assert span.attributes["source_type"] == "incidentio"

    async def test_scribe_message_has_correct_shape(self) -> None:
        """Scribe message contains source_type, entity_id, output fields, and computed hashes."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=["Root cause text", "Resolution text"]
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, _make_config())
        request = _make_request()
        await handler.enrich(request)

        sqs.send_message.assert_awaited_once()
        call_kwargs = sqs.send_message.call_args[1]
        assert call_kwargs["queue_url"] == "https://sqs.example.com/scribe"

        body = json.loads(call_kwargs["message_body"])
        assert body["source_type"] == "incidentio"
        assert body["message_type"] == "enrichment"
        assert body["entity_id"]["incident_id"] == "INC-42"  # from entity_id
        assert body["updates"]["root_cause_summary"] == "Root cause text"
        assert body["updates"]["description_summary"] == "Resolution text"
        # Hashes are Enricher-computed, not forwarded from producer
        assert "root_cause_summary_hash" in body["hashes"]
        assert "content_hash" not in body["hashes"]  # no old entity_hash fields

    async def test_scribe_message_forwards_entered_at_unchanged(self) -> None:
        """DLQ retry staleness guard (ADR 024): the inbound request's
        entered_at is forwarded verbatim into the outbound Scribe message —
        the Enricher must never re-stamp it to "now", or a message stuck in
        the Enricher's own DLQ would look fresher than it actually is."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=["Root cause text", "Resolution text"]
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, _make_config())
        entered_at = datetime(2026, 1, 1, 12, 0, 0)
        request = _make_request(entered_at=entered_at)
        await handler.enrich(request)

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert body["entered_at"] == entered_at.isoformat()

    async def test_scribe_message_entered_at_null_when_request_has_none(self) -> None:
        """A request that predates the guard (no entered_at) forwards None,
        not a freshly-minted timestamp."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=["Root cause text", "Resolution text"]
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, _make_config())
        await handler.enrich(_make_request(entered_at=None))

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert body["entered_at"] is None

    async def test_scribe_message_uses_correct_queue_url(self) -> None:
        """Scribe message is sent to scribe_llm_queue_url from config."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="out",
                        prompt="p",
                        hash_field="out_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "test"}))

        call_kwargs = sqs.send_message.call_args[1]
        assert call_kwargs["queue_url"] == "https://sqs.example.com/scribe"


class TestEnrichmentHandlerMultiKeyInput:
    """Tests for multi-key input concatenation."""

    async def test_multi_key_input_combines_content_fields(self) -> None:
        """Input content from multiple keys is combined into the user message."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="combined result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary", "resolution_statement"],
                        output_field="combined_summary",
                        prompt="Summarize both.",
                        hash_field="combined_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        request = _make_request(
            content={
                "summary": "Service was down",
                "resolution_statement": "Restarted pods",
            }
        )
        await handler.enrich(request)

        # Check that the user message contains content from both keys
        call_args = facade.send_message_with_retry.call_args
        messages = call_args[1]["messages"]
        user_message_content = messages[1].content
        assert "Service was down" in user_message_content
        assert "Restarted pods" in user_message_content
        assert "Summary:" in user_message_content
        assert "Resolution Statement:" in user_message_content

    async def test_missing_content_key_uses_empty_string(self) -> None:
        """Missing content key in request.content uses empty string gracefully."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary", "nonexistent_field"],
                        output_field="out",
                        prompt="p",
                        hash_field="out_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        request = _make_request(content={"summary": "some text"})

        # Should not raise
        await handler.enrich(request)
        facade.send_message_with_retry.assert_awaited_once()


class TestEnrichmentHandlerErrorHandling:
    """Tests for error handling in EnrichmentHandler."""

    async def test_content_filtered_error_wrapped(self) -> None:
        """FacadeContentFilteredError from Facade is wrapped in ContentFilteredError."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=FacadeContentFilteredError("blocked")
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)

        with pytest.raises(ContentFilteredError):
            await handler.enrich(_make_request(content={"summary": "bad content"}))

    async def test_facade_bad_request_error_propagates(self) -> None:
        """FacadeBadRequestError propagates as-is without wrapping."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=FacadeBadRequestError("invalid model")
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="out",
                        prompt="p",
                        hash_field="out_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)

        with pytest.raises(FacadeBadRequestError):
            await handler.enrich(_make_request(content={"summary": "text"}))

    async def test_empty_facade_response_stored_as_none(self) -> None:
        """Empty string response from Facade is stored as None in Scribe message."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "text"}))

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert body["updates"]["root_cause_summary"] is None

    async def test_general_exception_propagates_for_retry(self) -> None:
        """Unexpected exceptions from Facade propagate for consumer loop retry."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=ConnectionError("network failure")
        )
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="out",
                        prompt="p",
                        hash_field="out_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)

        with pytest.raises(ConnectionError):
            await handler.enrich(_make_request(content={"summary": "text"}))

    async def test_pre_built_prompt_uses_general_prompt_template(self) -> None:
        """Handler pre-builds prompts by combining general_prompt with source prompt."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="out",
                        prompt="Source-specific instructions.",
                        hash_field="out_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "text"}))

        call_args = facade.send_message_with_retry.call_args
        messages = call_args[1]["messages"]
        system_content = messages[0].content
        assert "General:" in system_content
        assert "Source-specific instructions." in system_content

    async def test_call_facade_passes_output_field_as_operation(self) -> None:
        """_call_facade passes the output_field name as the operation kwarg to Facade."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "text"}))

        call_args = facade.send_message_with_retry.call_args
        assert call_args[1]["operation"] == "root_cause_summary"

    async def test_enricher_metrics_record_per_operation_on_success(self) -> None:
        """When enricher_metrics is provided, record_enrichment_operation is called on success."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()
        enricher_metrics = MagicMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(
            facade, sqs, config, enricher_metrics=enricher_metrics
        )
        await handler.enrich(_make_request(content={"summary": "text"}))

        enricher_metrics.record_enrichment_operation.assert_called_once_with(
            "incidentio", "root_cause_summary", success=True
        )

    async def test_enricher_metrics_record_per_operation_on_failure(self) -> None:
        """When enricher_metrics is provided, record_enrichment_operation is called on failure."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        sqs = MagicMock()
        enricher_metrics = MagicMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(
            facade, sqs, config, enricher_metrics=enricher_metrics
        )

        with pytest.raises(ConnectionError):
            await handler.enrich(_make_request(content={"summary": "text"}))

        call_kwargs = enricher_metrics.record_enrichment_operation.call_args[1]
        assert call_kwargs["success"] is False
        assert isinstance(call_kwargs["error"], ConnectionError)


class TestEnrichmentHandlerTraceEntityId:
    """Tests for the entity_id/reference_id passed to traced_llm_operation."""

    @staticmethod
    def _patch_traced_llm_operation(
        monkeypatch: pytest.MonkeyPatch,
    ) -> list[dict[str, Any]]:
        """Replace traced_llm_operation with a fake that records its kwargs."""
        calls: list[dict[str, Any]] = []

        @contextmanager
        def _fake(*_args: Any, **kwargs: Any) -> Any:
            calls.append(kwargs)
            yield MagicMock()

        monkeypatch.setattr(
            "enricher.handlers.enrichment_handler.traced_llm_operation", _fake
        )
        return calls

    async def test_reference_id_merged_into_trace_entity_id_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """content.reference_id is added alongside entity_id on the trace."""
        calls = self._patch_traced_llm_operation(monkeypatch)
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(
            _make_request(content={"summary": "text", "reference_id": "INC-1234"})
        )

        assert calls[0]["entity_id"] == {
            "incident_id": "INC-42",
            "reference_id": "INC-1234",
        }

    async def test_entity_id_unchanged_when_no_reference_id_in_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a reference_id in content, entity_id is passed through as-is."""
        calls = self._patch_traced_llm_operation(monkeypatch)
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "text"}))

        assert calls[0]["entity_id"] == {"incident_id": "INC-42"}


class TestEnrichmentHandlerHashLogic:
    """Tests for hash computation, comparison, and skip logic."""

    async def test_hash_fields_included_in_scribe_message(self) -> None:
        """Per-field hashes are included in Scribe message using configured DB column names."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": "DB down"}))

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert "root_cause_summary_hash" in body["hashes"]
        assert body["hashes"]["root_cause_summary_hash"] == generate_string_hash(
            "DB down"
        )

    async def test_hash_of_empty_string_included_in_scribe_message(self) -> None:
        """Empty content value hashes the empty string, so the hash is stored in Scribe message."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)
        await handler.enrich(_make_request(content={"summary": ""}))

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        # Empty value → hash of "" is stored, preventing infinite re-processing
        assert body["hashes"]["root_cause_summary_hash"] == generate_string_hash("")

    async def test_empty_content_not_reprocessed_on_second_message(self) -> None:
        """Entity with empty content is not re-processed on a second identical message."""
        summary_hash = generate_string_hash("")
        matik_api = _make_api_client_mock({"root_cause_summary_hash": summary_hash})

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config, matik_api_client=matik_api)
        await handler.enrich(_make_request(content={"summary": ""}))

        # Hash of "" matches stored hash — mapping is skipped
        facade.send_message_with_retry.assert_not_awaited()
        sqs.send_message.assert_not_awaited()

    async def test_hash_is_deterministic(self) -> None:
        """Same content value produces the same hash across multiple calls."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config)

        await handler.enrich(_make_request(content={"summary": "same content"}))
        sqs.send_message.reset_mock()
        await handler.enrich(_make_request(content={"summary": "same content"}))

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert body["hashes"]["root_cause_summary_hash"] == generate_string_hash(
            "same content"
        )

    async def test_all_hashes_match_skips_facade_and_scribe(self) -> None:
        """When all input key hashes match existing, enrich() returns without Facade or Scribe."""
        summary_hash = generate_string_hash("DB down")
        matik_api = _make_api_client_mock({"root_cause_summary_hash": summary_hash})

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock()
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config, matik_api_client=matik_api)
        await handler.enrich(_make_request(content={"summary": "DB down"}))

        facade.send_message_with_retry.assert_not_awaited()
        sqs.send_message.assert_not_awaited()

    async def test_partial_hash_match_only_processes_changed_mappings(self) -> None:
        """When one mapping's hash matches but another's differs, only changed mapping calls Facade."""
        # root_cause_summary mapping: inputs ["summary", "resolution_statement"]
        # combined = "DB down||Restarted" → stored hash matches → skip
        rc_hash = generate_string_hash("DB down||Restarted")
        # description_summary mapping: inputs ["name", "summary"]
        # combined = "||DB down" → stored hash is stale → process
        matik_api = _make_api_client_mock(
            {
                "root_cause_summary_hash": rc_hash,
                # description_hash absent → process description_summary
            }
        )

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="resolved")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=matik_api
        )
        await handler.enrich(
            _make_request(
                content={
                    "summary": "DB down",
                    "resolution_statement": "Restarted",
                }
            )
        )

        # Only description_summary mapping processed
        assert facade.send_message_with_retry.await_count == 1

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert "description_summary" in body["updates"]
        assert "root_cause_summary" not in body["updates"]
        assert "description_hash" in body["hashes"]

    async def test_no_existing_hashes_processes_all_mappings(self) -> None:
        """When no existing hashes (new entity), all mappings are processed."""
        matik_api = _make_api_client_mock({})

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=matik_api
        )
        await handler.enrich(_make_request())

        assert facade.send_message_with_retry.await_count == 2

    async def test_api_failure_proceeds_with_full_enrichment(self) -> None:
        """When API call fails, enrichment proceeds for all mappings (safe default)."""
        matik_api = MagicMock()
        matik_api.post_json_request = MagicMock(side_effect=ConnectionError("API down"))

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=matik_api
        )
        await handler.enrich(_make_request())

        # All mappings processed despite API failure
        assert facade.send_message_with_retry.await_count == 2
        sqs.send_message.assert_awaited_once()

    async def test_shared_input_key_causes_both_mappings_to_regenerate(self) -> None:
        """Shared input key used by two mappings: if it changes, both mappings are regenerated."""
        # root_cause_summary: combined("old summary||same resolution") → stale
        # description_summary: combined("old name||old summary") → stale
        matik_api = _make_api_client_mock(
            {
                "root_cause_summary_hash": generate_string_hash(
                    "old summary||same resolution"
                ),
                "description_hash": generate_string_hash("old name||old summary"),
            }
        )

        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=matik_api
        )
        # Summary changed — both mappings include summary so both combined hashes change
        await handler.enrich(
            _make_request(
                content={
                    "name": "old name",
                    "summary": "new summary",
                    "resolution_statement": "same resolution",
                }
            )
        )

        # Both mappings regenerated because summary changed
        assert facade.send_message_with_retry.await_count == 2

    async def test_scribe_message_has_no_entity_hash_field(self) -> None:
        """Scribe message does not contain old-style entity_hash fields."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, _make_config())
        await handler.enrich(_make_request())

        body = json.loads(sqs.send_message.call_args[1]["message_body"])
        assert "content_hash" not in body["hashes"]
        assert "entity_hash" not in body["hashes"]

    async def test_no_api_client_processes_all_mappings(self) -> None:
        """Without matik_api_client (None), all mappings are processed as safe default."""
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        # matik_api_client defaults to None
        handler = EnrichmentHandler(facade, sqs, _make_config())
        await handler.enrich(_make_request())

        assert facade.send_message_with_retry.await_count == 2


class TestBatchHashFetching:
    """Tests for batch_fetch_hashes, _batch_fetch_from_api, and enrich() with pre-fetched hashes."""

    async def test_batch_fetch_no_api_client_returns_empty(self) -> None:
        """batch_fetch_hashes returns {} for all requests when no API client is configured."""
        handler = EnrichmentHandler(MagicMock(), MagicMock(), _make_config())
        requests = [_make_request(), _make_request()]
        result = await handler.batch_fetch_hashes(requests)
        # Keys present but values are empty dicts
        for req in requests:
            assert result[_canonical_entity_key(req.entity_id)] == {}

    async def test_batch_fetch_groups_by_source_type(self) -> None:
        """batch_fetch_hashes makes one batch API call per source_type."""
        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ],
                "jira": [
                    SourceMappingEntry(
                        input_keys=["issue_description"],
                        output_field="issue_summary",
                        prompt="p",
                        hash_field="summary_hash",
                    )
                ],
            }
        )
        api_mock = MagicMock()
        # Return empty results for both source types
        api_mock.post_json_request = MagicMock(
            return_value=json.dumps({"results": {}}).encode()
        )
        handler = EnrichmentHandler(
            MagicMock(), MagicMock(), config, matik_api_client=api_mock
        )

        req_incidentio = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="t1",
            entity_id={"incident_id": "INC-1"},
            content={"summary": "DB down"},
        )
        req_jira = EnrichmentRequest(
            source_type="jira",
            producer="historian",
            task_id="t2",
            entity_id={"issue_id": "JIRA-99"},
            content={"issue_description": "Bug"},
        )

        await handler.batch_fetch_hashes([req_incidentio, req_jira])

        # Should have made 2 API calls (one per source_type)
        assert api_mock.post_json_request.call_count == 2
        call_bodies = [c[0][1] for c in api_mock.post_json_request.call_args_list]
        source_types_called = {b["source_type"] for b in call_bodies}
        assert source_types_called == {"incidentio", "jira"}

    async def test_opens_named_span_per_source_type(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """batch_fetch_hashes opens one batch_fetch_hashes span per source_type."""
        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(
            return_value=json.dumps({"results": {}}).encode()
        )
        handler = EnrichmentHandler(
            MagicMock(), MagicMock(), _make_config(), matik_api_client=api_mock
        )
        req = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="t1",
            entity_id={"incident_id": "INC-1"},
            content={"summary": "DB down"},
        )

        await handler.batch_fetch_hashes([req])

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "batch_fetch_hashes"
        assert span.attributes is not None
        assert span.attributes["source_type"] == "incidentio"
        assert span.attributes["entity_count"] == 1

    async def test_batch_fetch_cache_hit_skips_api(self) -> None:
        """batch_fetch_hashes returns cached hashes without calling the API."""
        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(
            return_value=json.dumps({"results": {}}).encode()
        )
        cache = HashCache()
        entity_id = {"incident_id": "INC-42"}  # matches _make_request() default
        cache.put("incidentio", entity_id, {"root_cause_summary_hash": "cached_hash"})

        handler = EnrichmentHandler(
            MagicMock(),
            MagicMock(),
            _make_config(),
            matik_api_client=api_mock,
            hash_cache=cache,
        )
        req = _make_request()
        result = await handler.batch_fetch_hashes([req])

        api_mock.post_json_request.assert_not_called()
        assert result[_canonical_entity_key(entity_id)] == {
            "root_cause_summary_hash": "cached_hash"
        }

    async def test_batch_fetch_cache_miss_calls_api_and_populates_cache(self) -> None:
        """batch_fetch_hashes calls API on cache miss and populates cache with result."""
        entity_id = {"incident_id": "INC-42"}  # matches _make_request() default
        returned_hashes = {"root_cause_summary_hash": "api_hash"}
        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(
            return_value=json.dumps(
                {"results": {json.dumps(entity_id, sort_keys=True): returned_hashes}}
            ).encode()
        )
        cache = HashCache()
        handler = EnrichmentHandler(
            MagicMock(),
            MagicMock(),
            _make_config(),
            matik_api_client=api_mock,
            hash_cache=cache,
        )
        req = _make_request()
        result = await handler.batch_fetch_hashes([req])

        api_mock.post_json_request.assert_called_once()
        assert result[_canonical_entity_key(entity_id)] == returned_hashes
        # Cache should now hold the result
        assert cache.get("incidentio", entity_id) == returned_hashes

    async def test_batch_fetch_mixed_cache_hit_and_miss(self) -> None:
        """batch_fetch_hashes only calls API for cache misses, not for hits."""
        cache = HashCache()
        hit_entity = {"incident_id": "INC-hit"}
        miss_entity = {"incident_id": "INC-miss"}
        cache.put("incidentio", hit_entity, {"h": "cached"})

        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(
            return_value=json.dumps({"results": {}}).encode()
        )

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(
            MagicMock(),
            MagicMock(),
            config,
            matik_api_client=api_mock,
            hash_cache=cache,
        )

        req_hit = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="t1",
            entity_id=hit_entity,
            content={"summary": "a"},
        )
        req_miss = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="t2",
            entity_id=miss_entity,
            content={"summary": "b"},
        )

        result = await handler.batch_fetch_hashes([req_hit, req_miss])

        # Only one API call (for the miss); hit came from cache
        api_mock.post_json_request.assert_called_once()
        batch_call_body = api_mock.post_json_request.call_args[0][1]
        assert batch_call_body["entity_ids"] == [miss_entity]
        assert result[_canonical_entity_key(hit_entity)] == {"h": "cached"}

    async def test_batch_fetch_api_failure_falls_back_to_parallel_single(self) -> None:
        """When batch API fails, falls back to parallel individual calls."""
        single_hashes = {"root_cause_summary_hash": "single_hash"}

        call_count = 0

        def api_side_effect(endpoint: str, body: dict[str, Any]) -> bytes:
            nonlocal call_count
            call_count += 1
            if endpoint == "/v1/enrichment/hashes/batch":
                raise ConnectionError("batch endpoint not available")
            # Individual call succeeds
            return json.dumps(single_hashes).encode()

        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(side_effect=api_side_effect)

        handler = EnrichmentHandler(
            MagicMock(), MagicMock(), _make_config(), matik_api_client=api_mock
        )
        req = _make_request()
        result = await handler.batch_fetch_hashes([req])

        # Should have called batch (failed) then individual (succeeded)
        assert call_count == 2
        assert result[_canonical_entity_key(req.entity_id)] == single_hashes

    async def test_parallel_fetch_individual_gathers_concurrently(self) -> None:
        """_parallel_fetch_individual returns results for all entity_ids."""
        call_results = {
            json.dumps({"incident_id": "INC-1"}, sort_keys=True): {"h": "v1"},
            json.dumps({"incident_id": "INC-2"}, sort_keys=True): {"h": "v2"},
        }

        def api_side_effect(endpoint: str, body: dict[str, Any]) -> bytes:
            eid = body["entity_id"]
            key = json.dumps(eid, sort_keys=True)
            return json.dumps(call_results.get(key, {})).encode()

        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(side_effect=api_side_effect)

        handler = EnrichmentHandler(
            MagicMock(), MagicMock(), _make_config(), matik_api_client=api_mock
        )
        entity_ids = [{"incident_id": "INC-1"}, {"incident_id": "INC-2"}]
        result = await handler._parallel_fetch_individual("incidentio", entity_ids)

        assert result[json.dumps({"incident_id": "INC-1"}, sort_keys=True)] == {
            "h": "v1"
        }
        assert result[json.dumps({"incident_id": "INC-2"}, sort_keys=True)] == {
            "h": "v2"
        }
        assert api_mock.post_json_request.call_count == 2

    async def test_enrich_with_prefetched_hashes_skips_fetch(self) -> None:
        """enrich() uses pre-fetched hashes and does not call _fetch_existing_hashes."""
        summary_hash = generate_string_hash("DB down")
        # Pre-fetched hashes indicate content is unchanged
        existing = {"root_cause_summary_hash": summary_hash}

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        api_mock = MagicMock()
        api_mock.post_json_request = MagicMock(return_value=json.dumps({}).encode())
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(facade, sqs, config, matik_api_client=api_mock)
        await handler.enrich(
            _make_request(content={"summary": "DB down"}),
            existing_hashes=existing,
        )

        # Hash matches -> Facade and Scribe skipped; API never called
        api_mock.post_json_request.assert_not_called()
        facade.send_message_with_retry.assert_not_awaited()
        sqs.send_message.assert_not_awaited()

    async def test_enrich_updates_cache_after_scribe_publish(self) -> None:
        """enrich() updates the hash cache with changed hashes after publishing to Scribe."""
        cache = HashCache()
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result text")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        config = _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )
        handler = EnrichmentHandler(facade, sqs, config, hash_cache=cache)
        req = _make_request(content={"summary": "DB down"})
        await handler.enrich(req)

        # Cache should now contain the computed hash for this entity
        cached = cache.get("incidentio", {"incident_id": "INC-42"})
        assert cached is not None
        assert "root_cause_summary_hash" in cached
        assert cached["root_cause_summary_hash"] == generate_string_hash("DB down")

    async def test_enrich_without_prefetched_hashes_still_works(self) -> None:
        """enrich() without existing_hashes falls back to individual API fetch."""
        api_mock = _make_api_client_mock({})
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()

        handler = EnrichmentHandler(
            facade, sqs, _make_config(), matik_api_client=api_mock
        )
        # Call without existing_hashes -> should call individual API
        await handler.enrich(_make_request())

        api_mock.post_json_request.assert_called()
        facade.send_message_with_retry.assert_awaited()


class TestEnrichmentHandlerEnrichmentCacheMetric:
    """Tests for the per-mapping hash-dedup cache metric (hit/miss/new)."""

    def _single_mapping_config(self) -> EnricherConfig:
        return _make_config(
            {
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="p",
                        hash_field="root_cause_summary_hash",
                    )
                ]
            }
        )

    async def test_hit_recorded_when_hash_matches(self) -> None:
        """Matching hash records 'hit' and skips the Facade call."""
        matik_api = _make_api_client_mock(
            {"root_cause_summary_hash": generate_string_hash("DB down")}
        )
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock()
        sqs = MagicMock()
        sqs.send_message = AsyncMock()
        enricher_metrics = MagicMock()

        handler = EnrichmentHandler(
            facade,
            sqs,
            self._single_mapping_config(),
            enricher_metrics=enricher_metrics,
            matik_api_client=matik_api,
        )
        await handler.enrich(_make_request(content={"summary": "DB down"}))

        facade.send_message_with_retry.assert_not_awaited()
        enricher_metrics.record_enrichment_cache.assert_called_once_with(
            "incidentio", "root_cause_summary", "hit"
        )

    async def test_miss_recorded_when_hash_changed(self) -> None:
        """A stale existing hash records 'miss' and calls Facade."""
        matik_api = _make_api_client_mock({"root_cause_summary_hash": "stale_hash"})
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()
        enricher_metrics = MagicMock()

        handler = EnrichmentHandler(
            facade,
            sqs,
            self._single_mapping_config(),
            enricher_metrics=enricher_metrics,
            matik_api_client=matik_api,
        )
        await handler.enrich(_make_request(content={"summary": "DB down"}))

        facade.send_message_with_retry.assert_awaited_once()
        enricher_metrics.record_enrichment_cache.assert_called_once_with(
            "incidentio", "root_cause_summary", "miss"
        )

    async def test_new_recorded_when_no_existing_hash(self) -> None:
        """An absent existing hash records 'new' and calls Facade."""
        matik_api = _make_api_client_mock({})
        facade = MagicMock()
        facade.send_message_with_retry = AsyncMock(return_value="result")
        sqs = MagicMock()
        sqs.send_message = AsyncMock()
        enricher_metrics = MagicMock()

        handler = EnrichmentHandler(
            facade,
            sqs,
            self._single_mapping_config(),
            enricher_metrics=enricher_metrics,
            matik_api_client=matik_api,
        )
        await handler.enrich(_make_request(content={"summary": "DB down"}))

        facade.send_message_with_retry.assert_awaited_once()
        enricher_metrics.record_enrichment_cache.assert_called_once_with(
            "incidentio", "root_cause_summary", "new"
        )
