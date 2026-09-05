"""Tests for GenericHandler wired with the JIRA DataSourceSpec.

Verifies the registry-driven handler preserves the behavior of the former
hand-written JiraHandler: base upsert, the ``update_services`` base-column flag
(gating the ``services`` column via ``spec.base_column_flags``), the mixed-batch
routing guard, enrichment update, the tri-state return contract, and the
message-target enrichment hook.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.daos.base_dao import WriteOutcome
from common.datasources.registry import get_source
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.generic import GenericHandler
from scribe.hooks.runner import HookRunner

_SPEC = get_source("jira")

_VALID_JIRA_DATA = {
    "issue_id": "10001",
    "issue_key": "OPS-123",
    "ticket_type": "tcmr",
    "created_at": "2025-01-15T12:00:00Z",
}

_VALID_BASE_MSG = {
    "source_type": "jira",
    "message_type": "base",
    "data": _VALID_JIRA_DATA,
}

_VALID_ENRICHMENT_MSG = {
    "source_type": "jira",
    "message_type": "enrichment",
    "issue_key": "OPS-123",
    "issue_summary": "Auth service is returning 401s",
    "issue_comments_summary": "Team found the root cause",
    "summary_hash": "abc123",
    "comments_hash": "def456",
}

_VALID_ENRICHMENT_MSG_ENVELOPE = {
    "source_type": "jira",
    "message_type": "enrichment",
    "entity_id": {"issue_key": "OPS-123"},
    "updates": {
        "issue_summary": "Auth service is returning 401s",
        "issue_comments_summary": "Team found the root cause",
    },
    "hashes": {"summary_hash": "abc123", "comments_hash": "def456"},
}


class TestGenericJiraBase:
    @pytest.mark.asyncio
    async def test_success_calls_dao_upsert(self) -> None:
        """Valid base message calls upsert_batch with the parsed model."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.upsert_batch.assert_called_once()
        issues_arg, _dropped_arg = dao.upsert_batch.call_args[0]
        assert issues_arg[0].issue_key == "OPS-123"
        assert issues_arg[0].ticket_type == "tcmr"

    @pytest.mark.asyncio
    async def test_update_services_true_by_default_drops_nothing(self) -> None:
        """update_services defaults True when absent → no columns dropped."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        _, dropped_arg = dao.upsert_batch.call_args[0]
        assert dropped_arg == []

    @pytest.mark.asyncio
    async def test_update_services_false_drops_services_column(self) -> None:
        """update_services=False in the payload drops the gated ``services`` column."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base({**_VALID_BASE_MSG, "update_services": False})

        _, dropped_arg = dao.upsert_batch.call_args[0]
        assert dropped_arg == ["services"]

    @pytest.mark.asyncio
    async def test_mixed_update_services_in_batch_raises_malformed(self) -> None:
        """A batch with mixed update_services values is rejected before any write."""
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="Mixed update_services"):
            await handler.handle_base_batch(
                [_VALID_BASE_MSG, {**_VALID_BASE_MSG, "update_services": False}]
            )
        dao.upsert_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base({"source_type": "jira", "message_type": "base"})
        dao.upsert_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "jira", "message_type": "base", "data": {}}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = None
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)


class TestGenericJiraEnrichment:
    @pytest.mark.asyncio
    async def test_success_calls_update(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            issue_key="OPS-123",
            issue_summary="Auth service is returning 401s",
            issue_comments_summary="Team found the root cause",
            summary_hash="abc123",
            comments_hash="def456",
        )

    @pytest.mark.asyncio
    async def test_enrichment_with_null_llm_fields(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(
            {
                "source_type": "jira",
                "message_type": "enrichment",
                "issue_key": "OPS-456",
            }
        )

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            issue_key="OPS-456",
            issue_summary=None,
            issue_comments_summary=None,
            summary_hash=None,
            comments_hash=None,
        )

    @pytest.mark.asyncio
    async def test_enricher_envelope_accepted(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG_ENVELOPE)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            issue_key="OPS-123",
            issue_summary="Auth service is returning 401s",
            issue_comments_summary="Team found the root cause",
            summary_hash="abc123",
            comments_hash="def456",
        )

    @pytest.mark.asyncio
    async def test_missing_issue_key_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_enrichment(
                {"source_type": "jira", "message_type": "enrichment"}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.ERROR
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(RuntimeError, match="LLM field update failed"):
            await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

    @pytest.mark.asyncio
    async def test_enrichment_base_not_found_raises(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.BASE_NOT_FOUND
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(
            BaseRecordNotFoundError, match="base record not yet present"
        ):
            await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)


class TestGenericJiraHooks:
    @pytest.mark.asyncio
    async def test_enrichment_hook_runs_on_message(self) -> None:
        """jira hook target is 'message': the hook receives the enrichment msg."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        handler = GenericHandler(
            _SPEC, dao, hooks=HandlerHooks(enrichment=HookRunner([hook], None))
        )

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        hook.run.assert_awaited_once()
        assert hook.run.call_args[0][0].issue_key == "OPS-123"
