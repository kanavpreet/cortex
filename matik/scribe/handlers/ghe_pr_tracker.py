"""Handler for GHE PR tracker base Scribe messages."""

import asyncio
from typing import Any

from pydantic import ValidationError

from common.daos.ghe_pr_tracker_dao import GHEPRTrackerDAO
from common.models.ghe_pr_tracker import GHEPRTracker
from common.utils import log_utils

from ..errors import MalformedMessageError
from ._base import HandlerHooks

logger = log_utils.get_logger(__name__)


class GHEPRTrackerHandler:
    """Handles GHE PR tracker base events (no enrichment supported)."""

    def __init__(self, dao: GHEPRTrackerDAO, hooks: HandlerHooks | None = None) -> None:
        self._dao = dao
        self._hooks = hooks if hooks is not None else HandlerHooks()

    async def handle_base_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Deserialize and upsert a batch of GHE PR tracker records.

        No batch DAO method exists for trackers, so each record is upserted
        individually. Per-record exceptions propagate to the caller (batcher).

        Args:
            parsed_list: List of decoded JSON dicts with 'data' key containing
                GHEPRTracker fields.

        Raises:
            MalformedMessageError: If any 'data' field is missing or fails validation.
            RuntimeError: If the DAO write returns falsy (DB error).
        """
        for parsed in parsed_list:
            await self._handle_base_single(parsed)

    async def _handle_base_single(self, parsed: dict[str, Any]) -> None:
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise MalformedMessageError(
                "GHE PR tracker base message missing or invalid 'data' field"
            )

        try:
            tracker = GHEPRTracker.model_validate(data)
        except ValidationError as e:
            raise MalformedMessageError(f"GHEPRTracker validation failed: {e}") from e

        result = await asyncio.to_thread(self._dao.upsert_tracker, tracker)
        if not result:
            raise RuntimeError(
                f"DB upsert failed for org_id={tracker.org_id!r}, repo_id={tracker.repo_id!r}"
            )

        logger.info(
            "scribe wrote ghe_pr_tracker base event",
            org_id=tracker.org_id,
            repo_id=tracker.repo_id,
            cutoff_date=tracker.cutoff_date,
        )

        await self._hooks.base.run_all(tracker)

    async def handle_base(self, parsed: dict[str, Any]) -> None:
        """Deserialize and upsert a single GHE PR tracker record."""
        await self._handle_base_single(parsed)

    async def handle_enrichment_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Not supported — tracker records have no LLM enrichment.

        Raises:
            MalformedMessageError: Always.
        """
        raise MalformedMessageError(
            "ghe_pr_tracker does not support enrichment messages"
        )

    async def handle_enrichment(self, parsed: dict[str, Any]) -> None:
        """Not supported — tracker records have no LLM enrichment.

        Raises:
            MalformedMessageError: Always, since enrichment is not valid for trackers.
        """
        raise MalformedMessageError(
            "ghe_pr_tracker does not support enrichment messages"
        )
