"""Tests for GenericHandler behaviors not exercised by any registered spec.

The registry currently has no source that sets ``base_partial_update`` /
``base_message_model``, and every registered source sets an
``enrichment_message_model``. Those branches (partial-update base writes, and
the "no enrichment route" guard) are still real, reachable code paths in
``GenericHandler`` — this file exercises them directly against synthetic
``DataSourceSpec`` instances rather than a registered one.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from common.datasources.registry import DataSourceSpec
from common.models.incidentio_incident import IncidentIOIncident
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.handlers._base import HandlerHooks
from scribe.handlers.generic import GenericHandler
from scribe.hooks.runner import HookRunner


class _PartialBaseMessage(BaseModel):
    source_type: str
    message_type: str
    reference_id: str
    services: list[str] | None = None


_PARTIAL_SPEC = DataSourceSpec(
    source_type="test_partial",
    record_model=IncidentIOIncident,
    conflict_keys=["incident_id", "reference_id"],
    base_message_model=_PartialBaseMessage,
    base_partial_update="patch_services",
)

_NO_ENRICHMENT_SPEC = DataSourceSpec(
    source_type="test_no_enrichment",
    record_model=IncidentIOIncident,
    conflict_keys=["incident_id", "reference_id"],
)

_VALID_PARTIAL_MSG = {
    "source_type": "test_partial",
    "message_type": "base",
    "reference_id": "REF-1",
    "services": ["checkout"],
}


class TestGenericBasePartialUpdate:
    @pytest.mark.asyncio
    async def test_handle_base_batch_dispatches_to_partial_update(self) -> None:
        """A base_partial_update spec routes through the partial path, not upsert_batch."""
        dao = MagicMock()
        dao.patch_services.return_value = True
        handler = GenericHandler(_PARTIAL_SPEC, dao)

        await handler.handle_base_batch([_VALID_PARTIAL_MSG])

        dao.patch_services.assert_called_once_with(
            reference_id="REF-1", services=["checkout"]
        )
        dao.upsert_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_calls_dao_write_and_hook(self) -> None:
        dao = MagicMock()
        dao.patch_services.return_value = True
        hook = MagicMock()
        hook.name = "test_hook"
        hook.run = AsyncMock()
        handler = GenericHandler(
            _PARTIAL_SPEC, dao, hooks=HandlerHooks(base=HookRunner([hook], None))
        )

        await handler.handle_base(_VALID_PARTIAL_MSG)

        dao.patch_services.assert_called_once_with(
            reference_id="REF-1", services=["checkout"]
        )
        hook.run.assert_awaited_once()
        assert hook.run.call_args[0][0].reference_id == "REF-1"

    @pytest.mark.asyncio
    async def test_invalid_message_raises_malformed(self) -> None:
        dao = MagicMock()
        handler = GenericHandler(_PARTIAL_SPEC, dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "test_partial", "message_type": "base"}
            )
        dao.patch_services.assert_not_called()

    @pytest.mark.asyncio
    async def test_dao_returns_none_raises_runtime_error(self) -> None:
        dao = MagicMock()
        dao.patch_services.return_value = None
        handler = GenericHandler(_PARTIAL_SPEC, dao)

        with pytest.raises(RuntimeError, match="partial base write failed"):
            await handler.handle_base(_VALID_PARTIAL_MSG)

    @pytest.mark.asyncio
    async def test_dao_returns_false_raises_base_record_not_found(self) -> None:
        dao = MagicMock()
        dao.patch_services.return_value = False
        handler = GenericHandler(_PARTIAL_SPEC, dao)

        with pytest.raises(
            BaseRecordNotFoundError, match="base record not yet present"
        ):
            await handler.handle_base(_VALID_PARTIAL_MSG)

    @pytest.mark.asyncio
    async def test_batch_processes_each_message_independently(self) -> None:
        dao = MagicMock()
        dao.patch_services.return_value = True
        handler = GenericHandler(_PARTIAL_SPEC, dao)

        other_msg = {**_VALID_PARTIAL_MSG, "reference_id": "REF-2"}
        await handler.handle_base_batch([_VALID_PARTIAL_MSG, other_msg])

        assert dao.patch_services.call_count == 2


class TestGenericEnrichmentNoRoute:
    @pytest.mark.asyncio
    async def test_handle_enrichment_raises_when_spec_has_no_enrichment_model(
        self,
    ) -> None:
        dao = MagicMock()
        handler = GenericHandler(_NO_ENRICHMENT_SPEC, dao)

        with pytest.raises(RuntimeError, match="has no enrichment route"):
            await handler.handle_enrichment(
                {"source_type": "test_no_enrichment", "message_type": "enrichment"}
            )
