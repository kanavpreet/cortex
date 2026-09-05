"""Tests for GHE PR Tracker DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from common.daos.ghe_pr_tracker_dao import GHEPRTrackerDAO
from common.models.ghe_pr_tracker import GHEPRTracker


class TestGHEPRTrackerDAO:
    """Test suite for GHEPRTrackerDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = GHEPRTrackerDAO(mock_engine)
        assert dao._engine is mock_engine


class TestGetTrackerCutoff:
    """Tests for get_tracker_cutoff method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_get_tracker_cutoff_found(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_cutoff returns cutoff when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        cutoff_date = datetime(2024, 1, 15, 10, 30, 0)
        mock_row = (cutoff_date,)
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        cutoff = dao.get_tracker_cutoff(org_id=1, repo_id=100)

        assert cutoff == cutoff_date
        conn.execute.assert_called_once()

    def test_get_tracker_cutoff_not_found(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_cutoff returns None when not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        cutoff = dao.get_tracker_cutoff(org_id=1, repo_id=999)

        assert cutoff is None
        conn.execute.assert_called_once()

    def test_get_tracker_cutoff_database_error(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_cutoff returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        with capture_logs() as cap_logs:
            cutoff = dao.get_tracker_cutoff(org_id=1, repo_id=100)

        assert cutoff is None
        error_logs = [
            log
            for log in cap_logs
            if log.get("event") == "error getting tracker cutoff"
        ]
        assert len(error_logs) == 1
        assert error_logs[0]["org_id"] == 1
        assert error_logs[0]["repo_id"] == 100
        assert error_logs[0]["exc_info"] is True


class TestUpsertTracker:
    """Tests for upsert_tracker method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_upsert_insert_when_not_exists(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert inserts when tracker doesn't exist."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: get_tracker_cutoff returns None (not found)
        find_result = MagicMock()
        find_result.fetchone.return_value = None

        # Second call: insert succeeds
        insert_result = MagicMock()

        conn.execute.side_effect = [find_result, insert_result]

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=50,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao.upsert_tracker(tracker)

        assert success is True
        assert conn.execute.call_count == 2
        conn.commit.assert_called_once()

    def test_upsert_update_when_exists(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test upsert updates when tracker exists."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # First call: get_tracker_cutoff returns existing cutoff
        cutoff_date = datetime(2024, 1, 10, 0, 0, 0)
        find_result = MagicMock()
        find_result.fetchone.return_value = (cutoff_date,)

        # Second call: update succeeds
        update_result = MagicMock()

        conn.execute.side_effect = [find_result, update_result]

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=75,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao.upsert_tracker(tracker)

        assert success is True
        assert conn.execute.call_count == 2
        conn.commit.assert_called_once()


class TestInsertTracker:
    """Tests for _insert_tracker method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_insert_success(self, dao: GHEPRTrackerDAO, mock_engine: MagicMock) -> None:
        """Test successful insert returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=50,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao._insert_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_insert_database_error(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test insert returns False on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Duplicate key error")

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=50,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao._insert_tracker(tracker)

        assert success is False


class TestUpdateTracker:
    """Tests for _update_tracker method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_update_success(self, dao: GHEPRTrackerDAO, mock_engine: MagicMock) -> None:
        """Test successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=75,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao._update_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_database_error(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test update returns False on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=75,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        success = dao._update_tracker(tracker)

        assert success is False


class TestGetAllTrackers:
    """Tests for get_all_trackers method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine."""
        return GHEPRTrackerDAO(mock_engine)

    def test_get_all_trackers_success(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_all_trackers returns list of trackers."""
        conn = mock_engine.connect.return_value.__enter__.return_value

        # Create mock rows
        mock_row1 = MagicMock()
        mock_row1._mapping = {
            "id": 1,
            "org_id": 1,
            "repo_id": 100,
            "cutoff_date": datetime(2024, 1, 15, 10, 30, 0),
            "prs_crawled_count": 50,
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
            "updated_at": datetime(2024, 1, 10, 0, 0, 0),
        }
        mock_row2 = MagicMock()
        mock_row2._mapping = {
            "id": 2,
            "org_id": 1,
            "repo_id": 200,
            "cutoff_date": datetime(2024, 1, 20, 12, 0, 0),
            "prs_crawled_count": 100,
            "created_at": datetime(2024, 1, 5, 0, 0, 0),
            "updated_at": datetime(2024, 1, 15, 0, 0, 0),
        }

        result = MagicMock()
        result.fetchall.return_value = [mock_row1, mock_row2]
        conn.execute.return_value = result

        trackers = dao.get_all_trackers()

        assert trackers is not None
        assert len(trackers) == 2
        assert trackers[0].org_id == 1
        assert trackers[0].repo_id == 100
        assert trackers[1].repo_id == 200

    def test_get_all_trackers_empty(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_all_trackers returns empty list when no trackers exist."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        trackers = dao.get_all_trackers()

        assert trackers is not None
        assert len(trackers) == 0

    def test_get_all_trackers_database_error(
        self, dao: GHEPRTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_all_trackers returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        trackers = dao.get_all_trackers()

        assert trackers is None


class TestMetricsInstrumentation:
    """Tests for metrics instrumentation in DAO methods."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def mock_metrics(self) -> MagicMock:
        """Mock DBMetrics with start_query returning a mock recorder."""
        metrics = MagicMock()
        record_fn = MagicMock()
        metrics.start_query.return_value = record_fn
        return metrics

    @pytest.fixture
    def dao_with_metrics(
        self, mock_engine: MagicMock, mock_metrics: MagicMock
    ) -> GHEPRTrackerDAO:
        """Create DAO instance with mock engine and metrics."""
        return GHEPRTrackerDAO(mock_engine, mock_metrics)

    def test_get_tracker_cutoff_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_tracker_cutoff records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        cutoff_date = datetime(2024, 1, 15, 10, 30, 0)
        mock_row = (cutoff_date,)
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        dao_with_metrics.get_tracker_cutoff(org_id=1, repo_id=100)

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pr_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_tracker_cutoff_records_metrics_on_not_found(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_tracker_cutoff records metrics when not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        dao_with_metrics.get_tracker_cutoff(org_id=1, repo_id=999)

        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_tracker_cutoff_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_tracker_cutoff records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao_with_metrics.get_tracker_cutoff(org_id=1, repo_id=100)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_insert_tracker_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _insert_tracker records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=50,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        dao_with_metrics._insert_tracker(tracker)

        mock_metrics.start_query.assert_called_once_with("insert", "ghe_pr_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_insert_tracker_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _insert_tracker records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Insert error")
        conn.execute.side_effect = error

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=50,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        dao_with_metrics._insert_tracker(tracker)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_update_tracker_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _update_tracker records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=75,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        dao_with_metrics._update_tracker(tracker)

        mock_metrics.start_query.assert_called_once_with("update", "ghe_pr_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_update_tracker_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test _update_tracker records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Update error")
        conn.execute.side_effect = error

        tracker = GHEPRTracker(
            org_id=1,
            repo_id=100,
            cutoff_date=datetime(2024, 1, 15, 10, 30, 0),
            prs_crawled_count=75,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            updated_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        dao_with_metrics._update_tracker(tracker)

        mock_metrics.start_query.return_value.assert_called_once_with(error)

    def test_get_all_trackers_records_metrics_on_success(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_all_trackers records metrics on success."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchall.return_value = []
        conn.execute.return_value = result

        dao_with_metrics.get_all_trackers()

        mock_metrics.start_query.assert_called_once_with("select", "ghe_pr_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_all_trackers_records_metrics_on_error(
        self,
        dao_with_metrics: GHEPRTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test get_all_trackers records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        error = Exception("Database error")
        conn.execute.side_effect = error

        dao_with_metrics.get_all_trackers()

        mock_metrics.start_query.return_value.assert_called_once_with(error)
