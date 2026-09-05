"""Tests for CorrelationGroupHandler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from scribe.errors import MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.correlation_group import CorrelationGroupHandler
from scribe.hooks.runner import HookRunner

_VALID_GROUP_DATA = {
    "anchor_entity_id": "INC-99",
    "anchor_type": "incident",
    "correlation_timestamp": datetime(2025, 1, 15, 12, 0, 0).isoformat(),
}

_VALID_BASE_MSG = {
    "source_type": "correlation_group",
    "message_type": "base",
    "data": _VALID_GROUP_DATA,
}


class TestCorrelationGroupHandlerBase:
    @pytest.mark.asyncio
    async def test_success_calls_insert_or_update_group(self) -> None:
        """Valid base message calls insert_or_update_group with the parsed model."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = MagicMock()
        handler = CorrelationGroupHandler(dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.insert_or_update_group.assert_called_once()
        group_arg = dao.insert_or_update_group.call_args[0][0]
        assert group_arg.anchor_entity_id == "INC-99"
        assert group_arg.anchor_type == "incident"

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        """Missing 'data' field raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base(
                {"source_type": "correlation_group", "message_type": "base"}
            )

        dao.insert_or_update_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_dict_data_raises_malformed(self) -> None:
        """Non-dict 'data' raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(MalformedMessageError):
            await handler.handle_base(
                {
                    "source_type": "correlation_group",
                    "message_type": "base",
                    "data": "bad",
                }
            )

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        """Data missing required model fields raises MalformedMessageError."""
        dao = MagicMock()
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {
                    "source_type": "correlation_group",
                    "message_type": "base",
                    "data": {},
                }
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        """DAO returning None raises RuntimeError."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = None
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)

    @pytest.mark.asyncio
    async def test_all_optional_fields_deserialized(self) -> None:
        """Optional group fields are deserialized correctly."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = MagicMock()
        handler = CorrelationGroupHandler(dao)

        msg = {
            "source_type": "correlation_group",
            "message_type": "base",
            "data": {
                "anchor_entity_id": "INC-42",
                "anchor_type": "incident",
                "services": ["svc-auth", "svc-vpn"],
                "base_score": 0.75,
                "final_score": 0.75,
                "scoring_version": "incident_v1",
                "correlation_timestamp": datetime(2025, 1, 15, 12, 0, 0).isoformat(),
            },
        }
        await handler.handle_base(msg)

        group_arg = dao.insert_or_update_group.call_args[0][0]
        assert group_arg.anchor_entity_id == "INC-42"
        assert group_arg.services == ["svc-auth", "svc-vpn"]
        assert group_arg.base_score == 0.75
        assert group_arg.scoring_version == "incident_v1"


class TestCorrelationGroupHandlerHooks:
    @pytest.mark.asyncio
    async def test_base_hook_called_on_success(self) -> None:
        """Hook is invoked after a successful base DB write."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = MagicMock()

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = CorrelationGroupHandler(dao, hooks=HandlerHooks(base=runner))

        await handler.handle_base(_VALID_BASE_MSG)

        hook.run.assert_awaited_once()
        called_group = hook.run.call_args[0][0]
        assert called_group.anchor_entity_id == "INC-99"

    @pytest.mark.asyncio
    async def test_base_hook_not_called_on_dao_failure(self) -> None:
        """Hook is NOT invoked when the DAO write fails."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = None

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = CorrelationGroupHandler(dao, hooks=HandlerHooks(base=runner))

        with pytest.raises(RuntimeError):
            await handler.handle_base(_VALID_BASE_MSG)

        hook.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_hooks_by_default(self) -> None:
        """Handler with no hooks argument completes without error."""
        dao = MagicMock()
        dao.insert_or_update_group.return_value = MagicMock()
        handler = CorrelationGroupHandler(dao)

        await handler.handle_base(_VALID_BASE_MSG)


class TestCorrelationGroupHandlerEnrichment:
    @pytest.mark.asyncio
    async def test_enrichment_always_raises_malformed(self) -> None:
        """Correlation groups have no LLM fields — handle_enrichment always raises."""
        dao = MagicMock()
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(MalformedMessageError, match="does not support"):
            await handler.handle_enrichment(
                {"source_type": "correlation_group", "message_type": "enrichment"}
            )

        dao.insert_or_update_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_raises_regardless_of_payload(self) -> None:
        """handle_enrichment raises even when payload has valid-looking fields."""
        dao = MagicMock()
        handler = CorrelationGroupHandler(dao)

        with pytest.raises(MalformedMessageError):
            await handler.handle_enrichment(
                {
                    "source_type": "correlation_group",
                    "message_type": "enrichment",
                    "anchor_entity_id": "INC-99",
                }
            )
