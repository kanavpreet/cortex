"""Tests for GenericHandler wired with the Incident.io DataSourceSpec.

Verifies the registry-driven handler preserves the behavior of the former
hand-written IncidentIOHandler: base upsert, enrichment update, the tri-state
return contract, and the incidentio-specific "re-fetch the record and run the
enrichment hook on it" behavior (enrichment_hook_target="record").
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.daos.base_dao import WriteOutcome
from common.datasources.registry import get_source
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.generic import GenericHandler
from scribe.hooks.runner import HookRunner

_SPEC = get_source("incidentio")

# Minimum fields required by IncidentIOIncident for model_validate to succeed
_VALID_INCIDENT_DATA = {
    "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
    "reference_id": "INC-123",
    "severity": "minor",
    "status": "active",
    "slack_channel_id": "C12345678",
    "visibility": "public",
    "created_at": "2025-01-15T12:00:00Z",
    "reported_at": "2025-01-15T12:00:00Z",
    "updated_at": "2025-01-15T12:00:00Z",
}

_VALID_BASE_MSG = {
    "source_type": "incidentio",
    "message_type": "base",
    "data": _VALID_INCIDENT_DATA,
}

_VALID_ENRICHMENT_MSG = {
    "source_type": "incidentio",
    "message_type": "enrichment",
    "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
    "root_cause_summary": "VPN certificate expired",
    "root_cause_summary_hash": "abc123",
    "description_summary": "Brief summary",
    "description_hash": "def456",
}

_VALID_ENRICHMENT_MSG_ENVELOPE = {
    "source_type": "incidentio",
    "message_type": "enrichment",
    "entity_id": {"incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB"},
    "updates": {
        "root_cause_summary": "VPN certificate expired",
        "description_summary": "Brief summary",
    },
    "hashes": {"root_cause_summary_hash": "abc123", "description_hash": "def456"},
}


class TestGenericHandlerBase:
    @pytest.mark.asyncio
    async def test_success_calls_dao_upsert(self) -> None:
        """Valid base message deserializes and calls the spec-driven upsert_batch."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.upsert_batch.assert_called_once()
        incident_arg = dao.upsert_batch.call_args[0][0][0]
        assert incident_arg.incident_id == "01K3H5K30V3TECAF9G2HD1X5ZB"
        assert incident_arg.reference_id == "INC-123"

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base(
                {"source_type": "incidentio", "message_type": "base"}
            )

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "incidentio", "message_type": "base", "data": {}}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = None
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)

    @pytest.mark.asyncio
    async def test_entered_at_threaded_into_base_guard_column(self) -> None:
        """The message's top-level entered_at (ADR 024) lands on the record
        under the spec's base_entered_at_column, not left behind on the
        envelope — this is what lets _upsert_chunk's case() guard see it."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(
            {**_VALID_BASE_MSG, "entered_at": "2026-01-01T00:00:00Z"}
        )

        incident_arg = dao.upsert_batch.call_args[0][0][0]
        assert incident_arg.incidentio_base_entered_at is not None

    @pytest.mark.asyncio
    async def test_missing_entered_at_leaves_base_guard_column_unset(self) -> None:
        """A base message with no entered_at (predates the guard) leaves the
        guard column unset — _upsert_chunk falls back to unconditional."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        incident_arg = dao.upsert_batch.call_args[0][0][0]
        assert incident_arg.incidentio_base_entered_at is None


class TestGenericHandlerEnrichment:
    @pytest.mark.asyncio
    async def test_success_calls_update_llm_fields(self) -> None:
        """Enrichment message calls the spec-driven update with keyed kwargs."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        dao.find_incident_by_id.return_value = None
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="VPN certificate expired",
            root_cause_summary_hash="abc123",
            description_summary="Brief summary",
            description_hash="def456",
        )

    @pytest.mark.asyncio
    async def test_enrichment_with_null_llm_fields(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        dao.find_incident_by_id.return_value = None
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(
            {
                "source_type": "incidentio",
                "message_type": "enrichment",
                "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
            }
        )

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary=None,
            root_cause_summary_hash=None,
            description_summary=None,
            description_hash=None,
        )

    @pytest.mark.asyncio
    async def test_enricher_envelope_accepted(self) -> None:
        """Nested enricher envelope is flattened by the mixin and processed."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        dao.find_incident_by_id.return_value = None
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG_ENVELOPE)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            root_cause_summary="VPN certificate expired",
            root_cause_summary_hash="abc123",
            description_summary="Brief summary",
            description_hash="def456",
        )

    @pytest.mark.asyncio
    async def test_missing_incident_id_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_enrichment(
                {"source_type": "incidentio", "message_type": "enrichment"}
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

    @pytest.mark.asyncio
    async def test_enrichment_skipped_stale_is_a_no_op_success(self) -> None:
        """SKIPPED_STALE (ADR 024) is acked, not misread as base-not-found, and
        does not re-fetch the record or run the enrichment hook — the fresher
        write that beat this message already ran its hook."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.SKIPPED_STALE
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)  # must not raise

        dao.find_incident_by_id.assert_not_called()


class TestGenericHandlerHooks:
    @pytest.mark.asyncio
    async def test_base_hook_called_on_success(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = 1

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(base=runner))

        await handler.handle_base(_VALID_BASE_MSG)

        hook.run.assert_awaited_once()
        called_incident = hook.run.call_args[0][0]
        assert called_incident.incident_id == "01K3H5K30V3TECAF9G2HD1X5ZB"

    @pytest.mark.asyncio
    async def test_base_hook_not_called_on_dao_failure(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = None

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(base=runner))

        with pytest.raises(RuntimeError):
            await handler.handle_base(_VALID_BASE_MSG)

        hook.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_base_hook_not_called_for_stale_record(self) -> None:
        """A record that lost the base staleness guard (ADR 024) must not
        fire the base hook (e.g. incidentio's enigmatologist correlation)
        as if its write had won — upsert_batch's aggregate affected-row
        count can't tell base_dao's caller which individual records were
        actually written vs. kept-at-old-value by the case() guard, so
        find_stale_base_keys is consulted before running hooks."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        dao.find_stale_base_keys.return_value = {"01K3H5K30V3TECAF9G2HD1X5ZB"}

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(base=runner))

        await handler.handle_base(_VALID_BASE_MSG)

        dao.find_stale_base_keys.assert_called_once()
        hook.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_base_hook_called_when_not_stale(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        dao.find_stale_base_keys.return_value = set()

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(base=runner))

        await handler.handle_base(_VALID_BASE_MSG)

        dao.find_stale_base_keys.assert_called_once()
        hook.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_key_lookup_skipped_without_base_hooks(self) -> None:
        """No base hooks registered -> no reason to pay for the extra
        find_stale_base_keys round trip."""
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.find_stale_base_keys.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_hooks_by_default(self) -> None:
        dao = MagicMock()
        dao.upsert_batch.return_value = 1
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_base(_VALID_BASE_MSG)

    @pytest.mark.asyncio
    async def test_enrichment_hook_called_with_full_incident(self) -> None:
        """Incident.io's hook target is 'record': the hook receives the full
        IncidentIOIncident re-fetched via find_incident_by_id after the write."""
        from datetime import datetime

        from common.models.incidentio_incident import IncidentIOIncident

        full_incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-123",
            severity="minor",
            status="active",
            slack_channel_id="C12345678",
            visibility="public",
            created_at=datetime(2025, 1, 15, 12, 0, 0),
            reported_at=datetime(2025, 1, 15, 12, 0, 0),
            updated_at=datetime(2025, 1, 15, 12, 0, 0),
            description_summary="Brief summary",
            affected_services=["svc-a"],
        )
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = True
        dao.find_incident_by_id.return_value = full_incident

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.find_incident_by_id.assert_called_once_with("01K3H5K30V3TECAF9G2HD1X5ZB")
        hook.run.assert_awaited_once()
        called_arg = hook.run.call_args[0][0]
        assert called_arg.incident_id == "01K3H5K30V3TECAF9G2HD1X5ZB"
        assert called_arg.reference_id == "INC-123"

    @pytest.mark.asyncio
    async def test_enrichment_hook_skipped_when_incident_not_found(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = True
        dao.find_incident_by_id.return_value = None

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        hook.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enrichment_hook_not_called_on_dao_failure(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.ERROR

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        with pytest.raises(RuntimeError):
            await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        hook.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enrichment_hook_not_called_on_stale_skip(self) -> None:
        """A stale-skip (ADR 024) doesn't run the enrichment hook — the
        fresher write that beat this message already ran it."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.SKIPPED_STALE

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)  # must not raise

        hook.run.assert_not_awaited()
