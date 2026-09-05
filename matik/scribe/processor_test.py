"""Tests for ScribeProcessor."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from scribe.errors import MalformedMessageError
from scribe.processor import GroupKey, ScribeProcessor, ValidatedMessage


def _make_processor(
    handler: MagicMock | None = None,
) -> tuple[ScribeProcessor, MagicMock]:
    """Return a processor wired to a mock handler for 'incidentio'."""
    mock_handler = handler or MagicMock()
    mock_handler.handle_base = AsyncMock()
    mock_handler.handle_enrichment = AsyncMock()
    mock_handler.handle_base_batch = AsyncMock()
    mock_handler.handle_enrichment_batch = AsyncMock()
    processor = ScribeProcessor(handlers={"incidentio": mock_handler})
    return processor, mock_handler


class TestScribeProcessor:
    @pytest.mark.asyncio
    async def test_routes_base_to_handle_base(self) -> None:
        """Routes message_type='base' to handler.handle_base_batch."""
        processor, handler = _make_processor()
        body = json.dumps(
            {"source_type": "incidentio", "message_type": "base", "data": {}}
        )
        await processor.process(body)
        handler.handle_base_batch.assert_called_once()
        handler.handle_enrichment_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_routes_enrichment_to_handle_enrichment(self) -> None:
        """Routes message_type='enrichment' to handler.handle_enrichment_batch."""
        processor, handler = _make_processor()
        body = json.dumps(
            {
                "source_type": "incidentio",
                "message_type": "enrichment",
                "incident_id": "INC-1",
            }
        )
        await processor.process(body)
        handler.handle_enrichment_batch.assert_called_once()
        handler.handle_base_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_parsed_dict_to_handler(self) -> None:
        """Passes the full parsed dict (not just 'data') to the handler."""
        processor, handler = _make_processor()
        payload = {
            "source_type": "incidentio",
            "message_type": "base",
            "data": {"k": "v"},
        }
        await processor.process(json.dumps(payload))
        call_arg = handler.handle_base_batch.call_args[0][0]
        assert call_arg == [payload]

    @pytest.mark.asyncio
    async def test_invalid_json_raises_malformed(self) -> None:
        """Invalid JSON body raises MalformedMessageError."""
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="Invalid JSON"):
            await processor.process("not-json")

    @pytest.mark.asyncio
    async def test_non_dict_body_raises_malformed(self) -> None:
        """JSON array body raises MalformedMessageError."""
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="JSON object"):
            await processor.process(json.dumps([1, 2, 3]))

    @pytest.mark.asyncio
    async def test_missing_source_type_raises_malformed(self) -> None:
        """Missing source_type raises MalformedMessageError."""
        processor, _ = _make_processor()
        body = json.dumps({"message_type": "base"})
        with pytest.raises(MalformedMessageError, match="source_type"):
            await processor.process(body)

    @pytest.mark.asyncio
    async def test_missing_message_type_raises_malformed(self) -> None:
        """Missing message_type raises MalformedMessageError."""
        processor, _ = _make_processor()
        body = json.dumps({"source_type": "incidentio"})
        with pytest.raises(MalformedMessageError, match="message_type"):
            await processor.process(body)

    @pytest.mark.asyncio
    async def test_unknown_source_type_raises_malformed(self) -> None:
        """Unknown source_type raises MalformedMessageError."""
        processor, _ = _make_processor()
        body = json.dumps({"source_type": "unknown_source", "message_type": "base"})
        with pytest.raises(MalformedMessageError, match="Unknown source_type"):
            await processor.process(body)

    @pytest.mark.asyncio
    async def test_unknown_message_type_raises_malformed(self) -> None:
        """Unknown message_type raises MalformedMessageError."""
        processor, _ = _make_processor()
        body = json.dumps({"source_type": "incidentio", "message_type": "delete"})
        with pytest.raises(
            MalformedMessageError, match="not supported for source_type"
        ):
            await processor.process(body)

    @pytest.mark.asyncio
    async def test_correlation_enrichment_rejected_at_processor(self) -> None:
        """correlation + enrichment is rejected by the processor before reaching the handler."""
        h = MagicMock()
        h.handle_base_batch = AsyncMock()
        h.handle_enrichment_batch = AsyncMock()
        processor = ScribeProcessor(handlers={"correlation": h})

        body = json.dumps({"source_type": "correlation", "message_type": "enrichment"})
        with pytest.raises(
            MalformedMessageError, match="not supported for source_type"
        ):
            await processor.process(body)

        h.handle_base_batch.assert_not_called()
        h.handle_enrichment_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_correlation_group_base_routed(self) -> None:
        """correlation_group + base is routed to handle_base_batch."""
        h = MagicMock()
        h.handle_base_batch = AsyncMock()
        h.handle_enrichment_batch = AsyncMock()
        processor = ScribeProcessor(handlers={"correlation_group": h})

        body = json.dumps(
            {"source_type": "correlation_group", "message_type": "base", "data": {}}
        )
        await processor.process(body)

        h.handle_base_batch.assert_called_once()
        h.handle_enrichment_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_correlation_group_enrichment_rejected(self) -> None:
        """correlation_group + enrichment is rejected at the processor level."""
        h = MagicMock()
        h.handle_base_batch = AsyncMock()
        h.handle_enrichment_batch = AsyncMock()
        processor = ScribeProcessor(handlers={"correlation_group": h})

        body = json.dumps(
            {"source_type": "correlation_group", "message_type": "enrichment"}
        )
        with pytest.raises(
            MalformedMessageError, match="not supported for source_type"
        ):
            await processor.process(body)

        h.handle_base_batch.assert_not_called()
        h.handle_enrichment_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_exception_propagates(self) -> None:
        """Exceptions raised by handlers propagate unchanged for retry classification."""
        processor, handler = _make_processor()
        handler.handle_base_batch = AsyncMock(side_effect=RuntimeError("DB error"))
        body = json.dumps({"source_type": "incidentio", "message_type": "base"})
        with pytest.raises(RuntimeError, match="DB error"):
            await processor.process(body)

    @pytest.mark.asyncio
    async def test_all_source_types_routed(self) -> None:
        """Each source type is routed to its registered handler."""
        handlers: dict[str, MagicMock] = {}
        for source in (
            "incidentio",
            "ghe_pr",
            "jira",
            "incident_channel_summary",
            "correlation",
            "correlation_group",
        ):
            h = MagicMock()
            h.handle_base_batch = AsyncMock()
            h.handle_enrichment_batch = AsyncMock()
            handlers[source] = h

        processor = ScribeProcessor(handlers=handlers)

        for source in (
            "incidentio",
            "ghe_pr",
            "jira",
            "incident_channel_summary",
            "correlation",
            "correlation_group",
        ):
            body = json.dumps({"source_type": source, "message_type": "base"})
            await processor.process(body)
            handlers[source].handle_base_batch.assert_called_once()


class TestParseAndValidate:
    def test_happy_path_returns_validated_message(self) -> None:
        processor, _ = _make_processor()
        body = json.dumps(
            {"source_type": "incidentio", "message_type": "base", "data": {}}
        )
        result = processor.parse_and_validate(body)
        assert isinstance(result, ValidatedMessage)
        assert result.source_type == "incidentio"
        assert result.message_type == "base"
        assert result.parsed["data"] == {}

    def test_invalid_json_raises_malformed(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="Invalid JSON"):
            processor.parse_and_validate("not-json")

    def test_non_dict_raises_malformed(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="JSON object"):
            processor.parse_and_validate(json.dumps([1, 2]))

    def test_missing_source_type_raises(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="source_type"):
            processor.parse_and_validate(json.dumps({"message_type": "base"}))

    def test_missing_message_type_raises(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="message_type"):
            processor.parse_and_validate(json.dumps({"source_type": "incidentio"}))

    def test_unknown_source_type_raises(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="Unknown source_type"):
            processor.parse_and_validate(
                json.dumps({"source_type": "bogus", "message_type": "base"})
            )

    def test_unsupported_message_type_raises(self) -> None:
        processor, _ = _make_processor()
        with pytest.raises(MalformedMessageError, match="not supported"):
            processor.parse_and_validate(
                json.dumps({"source_type": "incidentio", "message_type": "delete"})
            )


class TestDispatchBatch:
    @pytest.mark.asyncio
    async def test_dispatches_base_batch_to_handler(self) -> None:
        processor, handler = _make_processor()
        key = GroupKey(source_type="incidentio", message_type="base")
        payloads = [
            {"source_type": "incidentio", "message_type": "base", "data": {"id": "1"}},
            {"source_type": "incidentio", "message_type": "base", "data": {"id": "2"}},
        ]
        await processor.dispatch_batch(key, payloads)
        handler.handle_base_batch.assert_called_once_with(payloads)
        handler.handle_enrichment_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_enrichment_batch_to_handler(self) -> None:
        processor, handler = _make_processor()
        key = GroupKey(source_type="incidentio", message_type="enrichment")
        payloads = [{"source_type": "incidentio", "message_type": "enrichment"}]
        await processor.dispatch_batch(key, payloads)
        handler.handle_enrichment_batch.assert_called_once_with(payloads)
        handler.handle_base_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_exception_propagates_from_dispatch(self) -> None:
        processor, handler = _make_processor()
        handler.handle_base_batch = AsyncMock(side_effect=RuntimeError("batch fail"))
        key = GroupKey(source_type="incidentio", message_type="base")
        with pytest.raises(RuntimeError, match="batch fail"):
            await processor.dispatch_batch(key, [{}])
