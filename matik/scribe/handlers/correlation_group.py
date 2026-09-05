"""Handler for reliability correlation group base Scribe messages."""

import asyncio
from typing import Any

from pydantic import ValidationError

from common.daos.reliability_correlation_group_dao import ReliabilityCorrelationGroupDAO
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup
from common.utils import log_utils

from ..errors import MalformedMessageError
from ._base import HandlerHooks

logger = log_utils.get_logger(__name__)


class CorrelationGroupHandler:
    """Handles reliability correlation group base events.

    Correlation groups have no LLM enrichment fields, so only base event writes
    are supported. Calling handle_enrichment raises MalformedMessageError.
    """

    def __init__(
        self, dao: ReliabilityCorrelationGroupDAO, hooks: HandlerHooks | None = None
    ) -> None:
        self._dao = dao
        self._hooks = hooks if hooks is not None else HandlerHooks()

    async def handle_base_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Deserialize and upsert a batch of reliability correlation group records.

        No batch DAO method exists for correlation groups, so each record is
        upserted individually. Per-record exceptions propagate to the caller.

        Args:
            parsed_list: List of decoded JSON dicts with 'data' key containing
                ReliabilityCorrelationGroup fields.

        Raises:
            MalformedMessageError: If any 'data' field is missing or fails validation.
            RuntimeError: If the DAO write returns None (DB error).
        """
        for parsed in parsed_list:
            await self._handle_base_single(parsed)

    async def _handle_base_single(self, parsed: dict[str, Any]) -> None:
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise MalformedMessageError(
                "Correlation group base message missing or invalid 'data' field"
            )

        try:
            group = ReliabilityCorrelationGroup.model_validate(data)
        except ValidationError as e:
            raise MalformedMessageError(
                f"ReliabilityCorrelationGroup validation failed: {e}"
            ) from e

        result = await asyncio.to_thread(self._dao.insert_or_update_group, group)
        if result is None:
            raise RuntimeError(
                f"DB upsert failed for anchor_entity_id={group.anchor_entity_id!r}"
            )

        logger.info(
            "scribe wrote correlation group",
            anchor_entity_id=group.anchor_entity_id,
        )

        await self._hooks.base.run_all(group)

    async def handle_base(self, parsed: dict[str, Any]) -> None:
        """Deserialize and upsert a single reliability correlation group record."""
        await self._handle_base_single(parsed)

    async def handle_enrichment_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Not supported — correlation groups have no LLM enrichment fields.

        Always raises MalformedMessageError.
        """
        raise MalformedMessageError(
            "source_type='correlation_group' does not support message_type='enrichment'"
        )

    async def handle_enrichment(self, parsed: dict[str, Any]) -> None:
        """Not supported — correlation groups have no LLM enrichment fields.

        Always raises MalformedMessageError.
        """
        raise MalformedMessageError(
            "source_type='correlation_group' does not support message_type='enrichment'"
        )
