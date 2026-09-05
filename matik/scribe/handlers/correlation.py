"""Handler for reliability correlation base Scribe messages."""

import asyncio
from typing import Any

from pydantic import ValidationError

from common.daos.reliability_correlation_dao import ReliabilityCorrelationDAO
from common.models.reliability_correlation import ReliabilityCorrelation
from common.utils import log_utils

from ..errors import MalformedMessageError
from ._base import HandlerHooks

logger = log_utils.get_logger(__name__)


class CorrelationHandler:
    """Handles reliability correlation base events.

    Correlations have no LLM enrichment fields, so only base event writes
    are supported. Calling handle_enrichment raises MalformedMessageError.
    """

    def __init__(
        self, dao: ReliabilityCorrelationDAO, hooks: HandlerHooks | None = None
    ) -> None:
        self._dao = dao
        self._hooks = hooks if hooks is not None else HandlerHooks()

    async def handle_base_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Deserialize and batch-upsert a list of reliability correlation records.

        Args:
            parsed_list: List of decoded JSON dicts with 'data' key containing
                ReliabilityCorrelation fields.

        Raises:
            MalformedMessageError: If any 'data' field is missing or fails validation.
            RuntimeError: If the DAO write returns None (DB error).
        """
        correlations: list[ReliabilityCorrelation] = []
        for parsed in parsed_list:
            data = parsed.get("data")
            if not isinstance(data, dict):
                raise MalformedMessageError(
                    "Correlation base message missing or invalid 'data' field"
                )
            try:
                correlations.append(ReliabilityCorrelation.model_validate(data))
            except ValidationError as e:
                raise MalformedMessageError(
                    f"ReliabilityCorrelation validation failed: {e}"
                ) from e

        result = await asyncio.to_thread(
            self._dao.upsert_correlations_batch, correlations
        )
        if result is None:
            ids = [c.anchor_entity_id for c in correlations]
            raise RuntimeError(f"DB upsert failed for anchor_entity_ids={ids!r}")

        logger.info(
            "scribe wrote correlation base events",
            count=len(correlations),
        )

        for correlation in correlations:
            await self._hooks.base.run_all(correlation)

    async def handle_base(self, parsed: dict[str, Any]) -> None:
        """Deserialize and upsert a single reliability correlation record."""
        await self.handle_base_batch([parsed])

    async def handle_enrichment_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Not supported — correlations have no LLM enrichment fields.

        Always raises MalformedMessageError.
        """
        raise MalformedMessageError(
            "source_type='correlation' does not support message_type='enrichment'"
        )

    async def handle_enrichment(self, parsed: dict[str, Any]) -> None:
        """Not supported — correlations have no LLM enrichment fields.

        Always raises MalformedMessageError.
        """
        raise MalformedMessageError(
            "source_type='correlation' does not support message_type='enrichment'"
        )
