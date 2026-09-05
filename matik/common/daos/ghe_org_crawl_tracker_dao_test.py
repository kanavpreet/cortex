"""Tests for GHE org crawl tracker DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.daos.ghe_org_crawl_tracker_dao import GHEOrgCrawlTrackerDAO
from common.models.ghe_org_crawl_tracker import GHEOrgCrawlTracker


def _make_engine() -> MagicMock:
    """Mock SQLAlchemy engine with connection context manager."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None
    return engine


@pytest.fixture
def mock_engine() -> MagicMock:
    """Mock SQLAlchemy engine with connection context manager."""
    return _make_engine()


@pytest.fixture
def dao(mock_engine: MagicMock) -> GHEOrgCrawlTrackerDAO:
    """Create DAO instance with mock engine."""
    return GHEOrgCrawlTrackerDAO(mock_engine)


@pytest.fixture
def sample_tracker() -> GHEOrgCrawlTracker:
    """Create a sample org crawl tracker."""
    return GHEOrgCrawlTracker(
        org_id=1,
        last_crawled_at=datetime(2024, 1, 15, 10, 30, 0),
        created_at=datetime(2024, 1, 1, 0, 0, 0),
        updated_at=datetime(2024, 1, 1, 0, 0, 0),
    )


class TestGetLastCrawledAt:
    """Tests for get_last_crawled_at."""

    def test_found(self, dao: GHEOrgCrawlTrackerDAO, mock_engine: MagicMock) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        ts = datetime(2024, 1, 15, 10, 30, 0)
        result = MagicMock()
        result.fetchone.return_value = (ts,)
        conn.execute.return_value = result

        assert dao.get_last_crawled_at(org_id=1) == ts
        conn.execute.assert_called_once()

    def test_not_found(
        self, dao: GHEOrgCrawlTrackerDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        assert dao.get_last_crawled_at(org_id=999) is None

    def test_database_error(
        self, dao: GHEOrgCrawlTrackerDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("boom")

        assert dao.get_last_crawled_at(org_id=1) is None


class TestUpsertWatermark:
    """Tests for upsert_watermark (atomic INSERT ... ON DUPLICATE KEY UPDATE)."""

    def test_upsert_success(
        self,
        dao: GHEOrgCrawlTrackerDAO,
        mock_engine: MagicMock,
        sample_tracker: GHEOrgCrawlTracker,
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        assert dao.upsert_watermark(sample_tracker) is True
        # Single atomic statement: no separate existence check.
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_upsert_is_atomic_on_duplicate_key(
        self,
        dao: GHEOrgCrawlTrackerDAO,
        mock_engine: MagicMock,
        sample_tracker: GHEOrgCrawlTracker,
    ) -> None:
        """The upsert issues one ON DUPLICATE KEY UPDATE statement (no read)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        dao.upsert_watermark(sample_tracker)

        executed_sql = str(conn.execute.call_args.args[0])
        assert "ON DUPLICATE KEY UPDATE" in executed_sql.upper()

    def test_upsert_database_error(
        self,
        dao: GHEOrgCrawlTrackerDAO,
        mock_engine: MagicMock,
        sample_tracker: GHEOrgCrawlTracker,
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("db error")

        assert dao.upsert_watermark(sample_tracker) is False


class TestMetrics:
    """Tests for DBMetrics instrumentation."""

    @pytest.fixture
    def mock_metrics(self) -> MagicMock:
        metrics = MagicMock()
        metrics.start_query.return_value = MagicMock()
        return metrics

    def test_get_records_metrics_on_success(self, mock_metrics: MagicMock) -> None:
        engine = _make_engine()
        conn = engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = (datetime(2024, 1, 1, 0, 0, 0),)
        conn.execute.return_value = result

        dao = GHEOrgCrawlTrackerDAO(engine, mock_metrics)
        dao.get_last_crawled_at(org_id=1)

        mock_metrics.start_query.assert_called_once_with(
            "select", "ghe_org_crawl_tracker"
        )
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_records_metrics_on_error(self, mock_metrics: MagicMock) -> None:
        engine = _make_engine()
        conn = engine.connect.return_value.__enter__.return_value
        error = Exception("boom")
        conn.execute.side_effect = error

        dao = GHEOrgCrawlTrackerDAO(engine, mock_metrics)
        dao.get_last_crawled_at(org_id=1)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_upsert_records_metrics_on_success(self, mock_metrics: MagicMock) -> None:
        engine = _make_engine()
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        dao = GHEOrgCrawlTrackerDAO(engine, mock_metrics)
        dao.upsert_watermark(
            GHEOrgCrawlTracker(org_id=1, last_crawled_at=datetime(2024, 1, 1, 0, 0, 0))
        )

        mock_metrics.start_query.assert_called_once_with(
            "upsert", "ghe_org_crawl_tracker"
        )
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_upsert_records_metrics_on_error(self, mock_metrics: MagicMock) -> None:
        engine = _make_engine()
        conn = engine.connect.return_value.__enter__.return_value
        error = Exception("boom")
        conn.execute.side_effect = error

        dao = GHEOrgCrawlTrackerDAO(engine, mock_metrics)
        dao.upsert_watermark(
            GHEOrgCrawlTracker(org_id=1, last_crawled_at=datetime(2024, 1, 1, 0, 0, 0))
        )

        mock_metrics.start_query.return_value.assert_called_once_with(error)
