"""Tests for GenericHandler wired with the GHE PR DataSourceSpec.

Verifies the registry-driven handler preserves the behavior of the former
hand-written GHEPRHandler: base upsert, enrichment update (keyed solely on
pull_request_id), the tri-state return contract, and the message-target
enrichment hook. Extra enricher envelope keys (org_id/repository_id) are ignored.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.daos.base_dao import WriteOutcome
from common.datasources.registry import get_source
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.generic import GenericHandler
from scribe.hooks.runner import HookRunner

_SPEC = get_source("ghe_pr")

_VALID_GHE_PR_DATA = {
    "pull_request_id": 999,
    "pull_request_number": 42,
    "org_id": 4,
    "org_login": "forge-apps",
    "repo_id": 100,
    "repo_name": "my-repo",
    "merged": True,
    "state": "closed",
    "locked": False,
    "created_at": "2025-01-15T12:00:00Z",
    "target_branch_name": "main",
}

_VALID_BASE_MSG = {
    "source_type": "ghe_pr",
    "message_type": "base",
    "data": _VALID_GHE_PR_DATA,
}

_VALID_ENRICHMENT_MSG = {
    "source_type": "ghe_pr",
    "message_type": "enrichment",
    "pull_request_id": 999,
    "pull_request_summary": "Rotates the VPN certificate to fix expiry issues",
    "description_hash": "abc123",
}

_VALID_ENRICHMENT_MSG_ENVELOPE = {
    "source_type": "ghe_pr",
    "message_type": "enrichment",
    "entity_id": {"org_id": 4, "repository_id": 100, "pull_request_id": 999},
    "updates": {
        "pull_request_summary": "Rotates the VPN certificate to fix expiry issues"
    },
    "hashes": {"description_hash": "abc123"},
}


class TestGenericGHEPRBase:
    @pytest.mark.asyncio
    async def test_success_calls_dao_upsert(self) -> None:
        """Valid base message deserializes and calls upsert_batch (no dropped cols)."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.upsert_batch.assert_called_once()
        prs_arg, dropped_arg = dao.upsert_batch.call_args[0]
        assert prs_arg[0].pull_request_id == 999
        assert prs_arg[0].org_login == "forge-apps"
        # ghe_pr declares no base_column_flags, so no columns are dropped.
        assert dropped_arg == []

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base({"source_type": "ghe_pr", "message_type": "base"})
        dao.upsert_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "ghe_pr", "message_type": "base", "data": {}}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = None
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)


class TestGenericGHEPREnrichment:
    @pytest.mark.asyncio
    async def test_success_calls_update(self) -> None:
        """Enrichment calls the spec-driven update keyed on pull_request_id."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            pull_request_id=999,
            pull_request_summary="Rotates the VPN certificate to fix expiry issues",
            description_hash="abc123",
        )

    @pytest.mark.asyncio
    async def test_enrichment_with_null_llm_fields(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(
            {
                "source_type": "ghe_pr",
                "message_type": "enrichment",
                "pull_request_id": 999,
            }
        )

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            pull_request_id=999,
            pull_request_summary=None,
            description_hash=None,
        )

    @pytest.mark.asyncio
    async def test_enricher_envelope_ignores_extra_entity_keys(self) -> None:
        """Nested envelope is flattened; org_id/repository_id are ignored."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG_ENVELOPE)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            pull_request_id=999,
            pull_request_summary="Rotates the VPN certificate to fix expiry issues",
            description_hash="abc123",
        )

    @pytest.mark.asyncio
    async def test_batch_processes_each_message(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment_batch(
            [_VALID_ENRICHMENT_MSG, _VALID_ENRICHMENT_MSG_ENVELOPE]
        )

        assert dao.update_llm_fields_from_message.call_count == 2

    @pytest.mark.asyncio
    async def test_missing_pr_id_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_enrichment(
                {
                    "source_type": "ghe_pr",
                    "message_type": "enrichment",
                    "repository_id": 100,
                }
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


class TestGenericGHEPRHooks:
    @pytest.mark.asyncio
    async def test_base_hook_called_with_pr(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        handler = GenericHandler(
            _SPEC, dao, hooks=HandlerHooks(base=HookRunner([hook], None))
        )

        await handler.handle_base(_VALID_BASE_MSG)

        hook.run.assert_awaited_once()
        assert hook.run.call_args[0][0].pull_request_id == 999

    @pytest.mark.asyncio
    async def test_enrichment_hook_runs_on_message(self) -> None:
        """ghe_pr hook target is 'message': the hook receives the enrichment msg."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        handler = GenericHandler(
            _SPEC, dao, hooks=HandlerHooks(enrichment=HookRunner([hook], None))
        )

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        # No record re-fetch for a message-target source.
        dao.find_ghe_pr.assert_not_called()
        hook.run.assert_awaited_once()
        assert hook.run.call_args[0][0].pull_request_id == 999
