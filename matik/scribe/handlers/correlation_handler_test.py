"""Tests for CorrelationHandler."""

from unittest.mock import MagicMock

import pytest

from scribe.errors import MalformedMessageError
from scribe.handlers.correlation import CorrelationHandler

_VALID_CORRELATION_DATA = {
    "anchor_entity_id": "INC-99",
    "correlation_type": "LLM",
    "entity_type": "github_pr",
    "entity_id": "PR-1",
}

_VALID_BASE_MSG = {
    "source_type": "correlation",
    "message_type": "base",
    "data": _VALID_CORRELATION_DATA,
}


class TestCorrelationHandlerBase:
    @pytest.mark.asyncio
    async def test_success_calls_upsert_batch(self) -> None:
        """Valid base message calls upsert_correlations_batch with parsed model."""
        dao = MagicMock()
        dao.upsert_correlations_batch.return_value = 1
        handler = CorrelationHandler(dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.upsert_correlations_batch.assert_called_once()
        correlations_arg = dao.upsert_correlations_batch.call_args[0][0]
        assert len(correlations_arg) == 1
        assert correlations_arg[0].anchor_entity_id == "INC-99"
        assert correlations_arg[0].entity_id == "PR-1"
        assert correlations_arg[0].entity_type == "github_pr"

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        """Missing 'data' field raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationHandler(dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base(
                {"source_type": "correlation", "message_type": "base"}
            )

        dao.upsert_correlations_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_dict_data_raises_malformed(self) -> None:
        """Non-dict 'data' raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationHandler(dao)

        with pytest.raises(MalformedMessageError):
            await handler.handle_base(
                {"source_type": "correlation", "message_type": "base", "data": "bad"}
            )

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        """Data missing required model fields raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationHandler(dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "correlation", "message_type": "base", "data": {}}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        """DAO returning None raises RuntimeError."""
        dao = MagicMock()
        dao.upsert_correlations_batch.return_value = None
        handler = CorrelationHandler(dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)

    @pytest.mark.asyncio
    async def test_all_required_correlation_fields_passed(self) -> None:
        """All required ReliabilityCorrelation fields are deserialized correctly."""
        dao = MagicMock()
        dao.upsert_correlations_batch.return_value = 1
        handler = CorrelationHandler(dao)

        msg = {
            "source_type": "correlation",
            "message_type": "base",
            "data": {
                "anchor_entity_id": "INC-42",
                "correlation_type": "SERVICE_MATCH",
                "entity_type": "jira_tcmr",
                "entity_id": "TCMR-99",
                "reasoning": "Same affected service",
                "base_score": 0.9,
                "final_score": 0.81,
            },
        }
        await handler.handle_base(msg)

        corr = dao.upsert_correlations_batch.call_args[0][0][0]
        assert corr.anchor_entity_id == "INC-42"
        assert corr.correlation_type == "SERVICE_MATCH"
        assert corr.entity_type == "jira_tcmr"
        assert corr.entity_id == "TCMR-99"
        assert corr.base_score == 0.9


class TestCorrelationHandlerEnrichment:
    @pytest.mark.asyncio
    async def test_enrichment_always_raises_malformed(self) -> None:
        """Correlations have no LLM fields — handle_enrichment always raises."""
        dao = MagicMock()
        handler = CorrelationHandler(dao)

        with pytest.raises(MalformedMessageError, match="does not support"):
            await handler.handle_enrichment(
                {"source_type": "correlation", "message_type": "enrichment"}
            )

        dao.upsert_correlations_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_raises_regardless_of_payload(self) -> None:
        """handle_enrichment raises even when payload has valid-looking fields."""
        dao = MagicMock()
        handler = CorrelationHandler(dao)

        with pytest.raises(MalformedMessageError):
            await handler.handle_enrichment(
                {
                    "source_type": "correlation",
                    "message_type": "enrichment",
                    "anchor_entity_id": "INC-99",
                    "entity_id": "PR-1",
                }
            )
