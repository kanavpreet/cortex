"""Tests for GHEPRTrackerHandler."""

from unittest.mock import MagicMock

import pytest

from scribe.errors import MalformedMessageError
from scribe.handlers.ghe_pr_tracker import GHEPRTrackerHandler

_VALID_TRACKER_DATA = {
    "org_id": 1,
    "repo_id": 2,
    "cutoff_date": "2025-01-01T00:00:00",
    "prs_crawled_count": 10,
    "created_at": "2025-01-15T12:00:00",
    "updated_at": "2025-01-15T12:00:00",
}

_VALID_BASE_MSG = {
    "source_type": "ghe_pr_tracker",
    "message_type": "base",
    "data": _VALID_TRACKER_DATA,
}


class TestGHEPRTrackerHandlerBase:
    @pytest.mark.asyncio
    async def test_success_calls_dao_upsert(self) -> None:
        """Valid base message calls upsert_tracker with the parsed model."""
        dao = MagicMock()
        dao.upsert_tracker.return_value = True
        handler = GHEPRTrackerHandler(dao)

        await handler.handle_base(_VALID_BASE_MSG)

        dao.upsert_tracker.assert_called_once()
        tracker_arg = dao.upsert_tracker.call_args[0][0]
        assert tracker_arg.org_id == 1
        assert tracker_arg.repo_id == 2
        assert tracker_arg.prs_crawled_count == 10

    @pytest.mark.asyncio
    async def test_missing_data_field_raises_malformed(self) -> None:
        """Missing 'data' field raises MalformedMessageError."""
        dao = MagicMock()
        handler = GHEPRTrackerHandler(dao)

        with pytest.raises(MalformedMessageError, match="missing or invalid 'data'"):
            await handler.handle_base(
                {"source_type": "ghe_pr_tracker", "message_type": "base"}
            )

        dao.upsert_tracker.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_dict_data_raises_malformed(self) -> None:
        """Non-dict 'data' raises MalformedMessageError."""
        dao = MagicMock()
        handler = GHEPRTrackerHandler(dao)

        with pytest.raises(MalformedMessageError):
            await handler.handle_base(
                {"source_type": "ghe_pr_tracker", "message_type": "base", "data": []}
            )

    @pytest.mark.asyncio
    async def test_invalid_data_raises_malformed(self) -> None:
        """Data missing required fields raises MalformedMessageError."""
        dao = MagicMock()
        handler = GHEPRTrackerHandler(dao)

        with pytest.raises(MalformedMessageError, match="validation failed"):
            await handler.handle_base(
                {"source_type": "ghe_pr_tracker", "message_type": "base", "data": {}}
            )

    @pytest.mark.asyncio
    async def test_dao_returns_false_raises_runtime_error(self) -> None:
        """DAO returning False raises RuntimeError."""
        dao = MagicMock()
        dao.upsert_tracker.return_value = False
        handler = GHEPRTrackerHandler(dao)

        with pytest.raises(RuntimeError, match="DB upsert failed"):
            await handler.handle_base(_VALID_BASE_MSG)


class TestGHEPRTrackerHandlerEnrichment:
    @pytest.mark.asyncio
    async def test_enrichment_always_raises_malformed(self) -> None:
        """handle_enrichment always raises MalformedMessageError."""
        dao = MagicMock()
        handler = GHEPRTrackerHandler(dao)

        with pytest.raises(MalformedMessageError, match="does not support enrichment"):
            await handler.handle_enrichment(
                {
                    "source_type": "ghe_pr_tracker",
                    "message_type": "enrichment",
                    "data": {},
                }
            )
