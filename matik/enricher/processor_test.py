"""Unit tests for EnricherProcessor."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.models.enricher_config import EnricherConfig, SourceMappingEntry
from enricher.exceptions import MalformedMessageError
from enricher.processor import EnricherProcessor


def _make_config(source_types: list[str] | None = None) -> EnricherConfig:
    """Create a minimal EnricherConfig with optional source_types.

    Args:
        source_types: List of source type names to register in source_mappings.
    """
    mappings: dict[str, list[SourceMappingEntry]] = {}
    for st in source_types or ["incidentio"]:
        mappings[st] = [
            SourceMappingEntry(
                input_keys=["summary"],
                output_field="root_cause_summary",
                prompt="You are an analyst.",
                hash_field="root_cause_summary_hash",
            )
        ]
    return EnricherConfig(
        enricher_queue_url="https://sqs.example.com/enricher",
        enricher_dlq_url="https://sqs.example.com/dlq",
        scribe_llm_queue_url="https://sqs.example.com/scribe",
        general_prompt="{source_instructions}",
        source_mappings=mappings,
    )


def _make_valid_body(source_type: str = "incidentio") -> str:
    """Return a JSON-encoded valid EnrichmentRequest body.

    Args:
        source_type: The source_type field value to use.
    """
    return json.dumps(
        {
            "source_type": source_type,
            "producer": "historian",
            "task_id": "task-001",
            "entity_id": {"incident_id": "INC-1"},
            "content": {"summary": "Database overload"},
        }
    )


class TestEnricherProcessorValidRouting:
    """Tests for valid message routing through EnricherProcessor."""

    async def test_valid_message_calls_handler_enrich(self) -> None:
        """Valid message dispatches to handler.enrich with correct EnrichmentRequest."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config()
        processor = EnricherProcessor(handler=handler, config=config)

        await processor.process(_make_valid_body())

        handler.enrich.assert_awaited_once()
        request = handler.enrich.call_args[0][0]
        assert request.source_type == "incidentio"
        assert request.task_id == "task-001"
        assert request.content == {"summary": "Database overload"}

    async def test_handler_exception_propagates(self) -> None:
        """Exceptions from handler.enrich propagate without wrapping."""
        handler = MagicMock()
        handler.enrich = AsyncMock(side_effect=RuntimeError("facade down"))
        config = _make_config()
        processor = EnricherProcessor(handler=handler, config=config)

        with pytest.raises(RuntimeError, match="facade down"):
            await processor.process(_make_valid_body())


class TestEnricherProcessorMalformedMessages:
    """Tests for malformed message handling."""

    async def test_invalid_json_raises_malformed_message_error(self) -> None:
        """Non-JSON body raises MalformedMessageError."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config()
        processor = EnricherProcessor(handler=handler, config=config)

        with pytest.raises(MalformedMessageError):
            await processor.process("not valid json {{{")

        handler.enrich.assert_not_awaited()

    async def test_schema_validation_failure_raises_malformed_message_error(
        self,
    ) -> None:
        """JSON with invalid schema raises MalformedMessageError."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config()
        processor = EnricherProcessor(handler=handler, config=config)

        # producer is required with Literal["historian", "chronicler"]
        invalid_body = json.dumps(
            {
                "source_type": "incidentio",
                "producer": "invalid_producer",
                "task_id": "t",
                "entity_id": {},
                "content": {},
            }
        )
        with pytest.raises(MalformedMessageError):
            await processor.process(invalid_body)

    async def test_missing_required_field_raises_malformed_message_error(self) -> None:
        """JSON missing required fields raises MalformedMessageError."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config()
        processor = EnricherProcessor(handler=handler, config=config)

        # Missing task_id
        invalid_body = json.dumps(
            {
                "source_type": "incidentio",
                "producer": "historian",
                "entity_id": {},
                "content": {},
            }
        )
        with pytest.raises(MalformedMessageError):
            await processor.process(invalid_body)


class TestEnricherProcessorUnknownSourceType:
    """Tests for unknown source_type validation."""

    async def test_unknown_source_type_raises_malformed_message_error(self) -> None:
        """Message with source_type not in source_mappings raises MalformedMessageError."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config(source_types=["incidentio"])
        processor = EnricherProcessor(handler=handler, config=config)

        body = _make_valid_body(source_type="pagerduty")

        with pytest.raises(MalformedMessageError):
            await processor.process(body)

        handler.enrich.assert_not_awaited()

    async def test_unknown_source_type_error_message_names_the_type(self) -> None:
        """MalformedMessageError for unknown source_type includes 'No mapping configured'."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config(source_types=["incidentio"])
        processor = EnricherProcessor(handler=handler, config=config)

        body = _make_valid_body(source_type="pagerduty")

        with pytest.raises(MalformedMessageError, match="No mapping configured"):
            await processor.process(body)

    async def test_known_source_type_does_not_raise(self) -> None:
        """Message with a configured source_type does not raise."""
        handler = MagicMock()
        handler.enrich = AsyncMock()
        config = _make_config(source_types=["incidentio", "ghe_pr"])
        processor = EnricherProcessor(handler=handler, config=config)

        body = _make_valid_body(source_type="ghe_pr")
        body_data = json.loads(body)
        body_data["content"] = {"original_description": "some PR"}
        await processor.process(json.dumps(body_data))

        handler.enrich.assert_awaited_once()
