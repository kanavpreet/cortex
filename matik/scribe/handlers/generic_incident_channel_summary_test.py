"""Tests for GenericHandler wired with the incident_channel_summary DataSourceSpec.

This source is enrichment-only (no base_message_model/base_partial_update):
the raw OpsBot channel summary is never persisted, so there is no base write
path to test here — only the standard enrichment path, mirroring
generic_incidentio_test.py's coverage style for its enrichment-only fields.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.daos.base_dao import WriteOutcome
from common.datasources.registry import get_source
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.generic import GenericHandler
from scribe.hooks.runner import HookRunner

_SPEC = get_source("incident_channel_summary")

_VALID_ENRICHMENT_MSG = {
    "source_type": "incident_channel_summary",
    "message_type": "enrichment",
    "reference_id": "INC-123",
    "incident_channel_summary": "LLM-condensed channel summary",
    "incident_channel_summary_hash": "abc123",
}

_VALID_ENRICHMENT_MSG_ENVELOPE = {
    "source_type": "incident_channel_summary",
    "message_type": "enrichment",
    "entity_id": {"reference_id": "INC-123"},
    "updates": {"incident_channel_summary": "LLM-condensed channel summary"},
    "hashes": {"incident_channel_summary_hash": "abc123"},
}


class TestGenericHandlerEnrichment:
    @pytest.mark.asyncio
    async def test_success_calls_update_llm_fields(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            reference_id="INC-123",
            incident_channel_summary="LLM-condensed channel summary",
            incident_channel_summary_hash="abc123",
        )

    @pytest.mark.asyncio
    async def test_enricher_envelope_accepted(self) -> None:
        """Nested enricher envelope is flattened by the mixin and processed."""
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        handler = GenericHandler(_SPEC, dao)

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG_ENVELOPE)

        dao.update_llm_fields_from_message.assert_called_once_with(
            entered_at=None,
            reference_id="INC-123",
            incident_channel_summary="LLM-condensed channel summary",
            incident_channel_summary_hash="abc123",
        )

    @pytest.mark.asyncio
    async def test_missing_reference_id_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_enrichment(
                {
                    "source_type": "incident_channel_summary",
                    "message_type": "enrichment",
                }
            )

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
    async def test_enrichment_hook_called_with_full_incident(self) -> None:
        """enrichment_hook_target='record': the hook receives the full
        IncidentIOIncident re-fetched via find_incident(reference_id), not the
        narrow enrichment message — so the enigmatologist correlation hook (shared
        with incidentio) can read description_summary/affected_services off it.
        """
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
            incident_channel_summary="LLM-condensed channel summary",
        )
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        dao.find_incident.return_value = full_incident

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.find_incident.assert_called_once_with("INC-123")
        hook.run.assert_awaited_once()
        called_arg = hook.run.call_args[0][0]
        assert called_arg.reference_id == "INC-123"
        assert called_arg.incident_channel_summary == "LLM-condensed channel summary"

    @pytest.mark.asyncio
    async def test_enrichment_hook_skipped_when_incident_not_found(self) -> None:
        dao = MagicMock()
        dao.update_llm_fields_from_message.return_value = WriteOutcome.WRITTEN
        dao.find_incident.return_value = None

        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        runner = HookRunner([hook], None)
        handler = GenericHandler(_SPEC, dao, hooks=HandlerHooks(enrichment=runner))

        await handler.handle_enrichment(_VALID_ENRICHMENT_MSG)

        dao.find_incident.assert_called_once_with("INC-123")
        hook.run.assert_not_awaited()
