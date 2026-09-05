"""Tests for Incident.io Tracker DAO."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.daos.incidentio_tracker_dao import IncidentIOTrackerDAO
from common.models.incidentio_tracker import IncidentIOTracker


class TestIncidentIOTrackerDAO:
    """Test suite for IncidentIOTrackerDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOTrackerDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOTrackerDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = IncidentIOTrackerDAO(mock_engine)
        assert dao._engine is mock_engine


class TestFindLastRecorded:
    """Tests for find_last_recorded method."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> IncidentIOTrackerDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOTrackerDAO(mock_engine)

    def test_find_last_recorded_success(
        self, dao: IncidentIOTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns tracker when found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "timestamp": datetime(2024, 1, 15, 10, 30, 0),
            "status": "OK",
            "error_message": None,
            "initial_sync_complete": True,
            "last_updated_at_cursor": datetime(2024, 1, 1, 0, 0, 0),
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        tracker = dao.find_last_recorded()

        assert tracker is not None
        assert tracker.id == 1
        assert tracker.initial_sync_complete is True
        assert tracker.status == "OK"
        assert tracker.error_message is None

    def test_find_last_recorded_not_found(
        self, dao: IncidentIOTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None when tracker not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        tracker = dao.find_last_recorded()

        assert tracker is None

    def test_find_last_recorded_database_error(
        self, dao: IncidentIOTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test find returns None on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database connection error")

        tracker = dao.find_last_recorded()

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
    def dao(self, mock_engine: MagicMock) -> IncidentIOTrackerDAO:
        """Create DAO instance with mock engine."""
        return IncidentIOTrackerDAO(mock_engine)

    def test_update_tracker_success(
        self, dao: IncidentIOTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test successful update returns True."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        conn.execute.return_value = result

        tracker = IncidentIOTracker(
            id=1,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="OK",
            error_message=None,
            initial_sync_complete=True,
        )
        success = dao.update_tracker(tracker)

        assert success is True
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_update_tracker_database_error(
        self, dao: IncidentIOTrackerDAO, mock_engine: MagicMock
    ) -> None:
        """Test update returns False on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Update error")

        tracker = IncidentIOTracker(
            id=1,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="ERROR",
            error_message="Connection timeout",
            initial_sync_complete=False,
        )
        success = dao.update_tracker(tracker)

        assert success is False


class TestTrackerDAOWithMetrics:
    """Tests for DAO operations with metrics instrumentation."""

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
        """Create mock DBMetrics."""
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        return metrics

    @pytest.fixture
    def dao_with_metrics(
        self, mock_engine: MagicMock, mock_metrics: MagicMock
    ) -> IncidentIOTrackerDAO:
        """Create DAO instance with mock engine and metrics."""
        return IncidentIOTrackerDAO(mock_engine, mock_metrics)

    def test_find_last_recorded_records_metrics_on_success(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_last_recorded records metrics on successful query."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "timestamp": datetime(2024, 1, 15, 10, 30, 0),
            "status": "OK",
            "error_message": None,
            "initial_sync_complete": True,
            "last_updated_at_cursor": None,
        }
        result = MagicMock()
        result.fetchone.return_value = mock_row
        conn.execute.return_value = result

        tracker = dao_with_metrics.find_last_recorded()

        assert tracker is not None
        mock_metrics.start_query.assert_called_once_with("select", "incidentio_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_last_recorded_records_metrics_on_not_found(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_last_recorded records metrics when not found."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result

        tracker = dao_with_metrics.find_last_recorded()

        assert tracker is None
        mock_metrics.start_query.assert_called_once()
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_find_last_recorded_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_last_recorded records metrics on database error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        tracker = dao_with_metrics.find_last_recorded()

        assert tracker is None
        mock_metrics.start_query.assert_called_once()
        mock_record = mock_metrics.start_query.return_value
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)

    def test_find_last_recorded_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test find_last_recorded handles exception from metrics recording."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        # Should not raise, just log warning
        tracker = dao_with_metrics.find_last_recorded()

        assert tracker is None

    def test_update_tracker_records_metrics_on_success(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_tracker records metrics on success."""
        tracker = IncidentIOTracker(
            id=1,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="OK",
            initial_sync_complete=True,
        )
        success = dao_with_metrics.update_tracker(tracker)

        assert success is True
        mock_metrics.start_query.assert_called_once_with("update", "incidentio_tracker")
        mock_metrics.start_query.return_value.assert_called_once_with(None)

    def test_update_tracker_records_metrics_on_error(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_tracker records metrics on error."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")

        tracker = IncidentIOTracker(
            id=1,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="ERROR",
            initial_sync_complete=False,
        )
        success = dao_with_metrics.update_tracker(tracker)

        assert success is False
        mock_record = mock_metrics.start_query.return_value
        assert isinstance(mock_record.call_args[0][0], Exception)

    def test_update_tracker_handles_metrics_exception(
        self,
        dao_with_metrics: IncidentIOTrackerDAO,
        mock_engine: MagicMock,
        mock_metrics: MagicMock,
    ) -> None:
        """Test update_tracker handles metrics exception gracefully."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("Database error")
        mock_metrics.start_query.return_value.side_effect = Exception("Metrics error")

        tracker = IncidentIOTracker(
            id=1,
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            status="ERROR",
            initial_sync_complete=False,
        )

        # Should not raise
        success = dao_with_metrics.update_tracker(tracker)

        assert success is False
