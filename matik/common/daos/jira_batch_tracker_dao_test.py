"""Tests for JIRA Batch Tracker DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.daos.jira_batch_tracker_dao import JiraBatchTrackerDAO
from common.models.jira_batch_tracker import JiraBatchTracker


class TestJiraBatchTrackerDAO:
    """Test suite for JiraBatchTrackerDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraBatchTrackerDAO:
        """Create DAO instance with mock engine."""
        return JiraBatchTrackerDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = JiraBatchTrackerDAO(mock_engine)
        assert dao._engine is mock_engine


class TestGetTrackerByType:
    """Tests for get_tracker_by_type method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraBatchTrackerDAO:
        """Create DAO instance with mock engine."""
        return JiraBatchTrackerDAO(mock_engine)

    def test_get_tracker_found(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_by_type returns tracker when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "ticket_type": "tcmr",
            "batch_start": datetime(2024, 1, 1, 0, 0, 0),
            "batch_end": datetime(2024, 1, 15, 0, 0, 0),
            "window_days": 14,
            "status": "OK",
            "error_message": None,
            "last_processed_at": datetime(2024, 1, 14, 12, 0, 0),
            "updated_at": datetime(2024, 1, 14, 12, 0, 0),
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        tracker = dao.get_tracker_by_type("tcmr")

        assert tracker is not None
        assert tracker.ticket_type == "tcmr"
        assert tracker.window_days == 14
        assert tracker.status == "OK"
        assert tracker.error_message is None

    def test_get_tracker_not_found(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_by_type returns None when not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        tracker = dao.get_tracker_by_type("nonexistent")

        assert tracker is None

    def test_get_tracker_database_error(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test get_tracker_by_type returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        tracker = dao.get_tracker_by_type("tcmr")

        assert tracker is None


class TestUpdateTracker:
    """Tests for update_tracker method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> JiraBatchTrackerDAO:
        """Create DAO instance with mock engine."""
        return JiraBatchTrackerDAO(mock_engine)

    def test_update_tracker_success(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 15, 0, 0, 0),
            batch_end=datetime(2024, 1, 29, 0, 0, 0),
            window_days=14,
            status="OK",
            error_message=None,
            last_processed_at=datetime(2024, 1, 28, 12, 0, 0),
            updated_at=datetime(2024, 1, 28, 12, 0, 0),
        )
        success = dao.update_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_tracker_database_error(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test update returns False on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 15, 0, 0, 0),
            batch_end=datetime(2024, 1, 29, 0, 0, 0),
            window_days=14,
            status="OK",
            updated_at=datetime(2024, 1, 28, 12, 0, 0),
        )
        success = dao.update_tracker(tracker)

        assert success is False

    def test_update_tracker_with_null_fields(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test update handles nullable fields correctly."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = JiraBatchTracker(
            ticket_type="operational",
            batch_start=datetime(2024, 1, 1, 0, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0, 0),
            window_days=14,
            status=None,  # Nullable
            error_message=None,  # Nullable
            last_processed_at=None,  # Nullable
            updated_at=datetime(2024, 1, 14, 12, 0, 0),
        )
        success = dao.update_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()

    def test_update_tracker_with_error_status(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test update with error status and message."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 15, 0, 0, 0),
            batch_end=datetime(2024, 1, 29, 0, 0, 0),
            window_days=14,
            status="ERROR",
            error_message="Failed to connect to JIRA API",
            last_processed_at=datetime(2024, 1, 20, 12, 0, 0),
            updated_at=datetime(2024, 1, 28, 12, 0, 0),
        )
        success = dao.update_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()


class TestMetricsRecording:
    """Exercises the optional DBMetrics record(...) branches on both methods."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def metrics(self) -> MagicMock:
        """Mock DBMetrics whose start_query returns a record() callback."""
        metrics = MagicMock()
        metrics.start_query.return_value = MagicMock()
        return metrics

    @pytest.fixture
    def dao(self, mock_engine: MagicMock, metrics: MagicMock) -> JiraBatchTrackerDAO:
        """Create DAO instance with mock engine and metrics."""
        return JiraBatchTrackerDAO(mock_engine, metrics)

    def _row(self) -> MagicMock:
        row = MagicMock()
        row._mapping = {
            "ticket_type": "tcmr",
            "batch_start": datetime(2024, 1, 1, 0, 0, 0),
            "batch_end": datetime(2024, 1, 15, 0, 0, 0),
            "window_days": 14,
            "status": "OK",
            "error_message": None,
            "last_processed_at": datetime(2024, 1, 14, 12, 0, 0),
            "updated_at": datetime(2024, 1, 14, 12, 0, 0),
        }
        return row

    def test_get_tracker_found_records_success(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock, metrics: MagicMock
    ) -> None:
        """Successful lookup records a success on the metric."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = self._row()
        conn.execute.return_value = result

        assert dao.get_tracker_by_type("tcmr") is not None
        metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_tracker_not_found_records_success(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock, metrics: MagicMock
    ) -> None:
        """A not-found lookup still records a success on the metric."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        assert dao.get_tracker_by_type("missing") is None
        metrics.start_query.return_value.assert_called_once_with(None)

    def test_get_tracker_error_records_exception(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock, metrics: MagicMock
    ) -> None:
        """A lookup error records the exception on the metric."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        boom = Exception("Database connection error")
        conn.execute.side_effect = boom

        assert dao.get_tracker_by_type("tcmr") is None
        metrics.start_query.return_value.assert_called_once_with(boom)

    def test_update_tracker_records_success(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock, metrics: MagicMock
    ) -> None:
        """A successful update records a success on the metric."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = MagicMock()

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 15, 0, 0, 0),
            batch_end=datetime(2024, 1, 29, 0, 0, 0),
            window_days=14,
            status="OK",
            updated_at=datetime(2024, 1, 28, 12, 0, 0),
        )
        assert dao.update_tracker(tracker) is True
        metrics.start_query.return_value.assert_called_once_with(None)

    def test_update_tracker_error_records_exception(
        self, dao: JiraBatchTrackerDAO, mock_engine: MagicMock, metrics: MagicMock
    ) -> None:
        """An update error records the exception on the metric."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        boom = Exception("Update error")
        conn.execute.side_effect = boom

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 15, 0, 0, 0),
            batch_end=datetime(2024, 1, 29, 0, 0, 0),
            window_days=14,
            status="OK",
            updated_at=datetime(2024, 1, 28, 12, 0, 0),
        )
        assert dao.update_tracker(tracker) is False
        metrics.start_query.return_value.assert_called_once_with(boom)
