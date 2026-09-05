"""Tests for ReliabilityCorrelationGroupDAO."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.daos.reliability_correlation_group_dao import ReliabilityCorrelationGroupDAO
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup


class TestReliabilityCorrelationGroupDAOInit:
    """Tests for DAO constructor."""

    def test_stores_engine_and_metrics(self) -> None:
        engine = MagicMock()
        metrics = MagicMock()
        dao = ReliabilityCorrelationGroupDAO(engine, metrics)
        assert dao._engine is engine
        assert dao._metrics is metrics

    def test_metrics_default_none(self) -> None:
        engine = MagicMock()
        dao = ReliabilityCorrelationGroupDAO(engine)
        assert dao._metrics is None


@pytest.fixture
def mock_engine() -> MagicMock:
    """Mock SQLAlchemy engine with connection context manager."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None
    return engine


@pytest.fixture
def dao(mock_engine: MagicMock) -> ReliabilityCorrelationGroupDAO:
    """Create DAO with mock engine."""
    return ReliabilityCorrelationGroupDAO(mock_engine)


def _make_group(**overrides: Any) -> ReliabilityCorrelationGroup:
    """Build a minimal ReliabilityCorrelationGroup."""
    defaults: dict[str, Any] = {
        "anchor_entity_id": "INC-99",
        "anchor_type": "incident",
        "correlation_timestamp": datetime(2025, 1, 15, 12, 0, 0),
    }
    defaults.update(overrides)
    return ReliabilityCorrelationGroup(**defaults)


class TestFindGroup:
    """Tests for find_group."""

    def test_returns_group_when_found(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        mock_row._mapping = {
            "id": 1,
            "anchor_entity_id": "INC-99",
            "anchor_type": "incident",
            "services": None,
            "start_time": None,
            "end_time": None,
            "correlation_timestamp": datetime(2025, 1, 15, 12, 0, 0),
            "base_score": 0.8,
            "final_score": 0.8,
            "scoring_version": "incident_v1",
            "feedback_list": None,
            "feedback_state": "NEUTRAL",
            "review_status": False,
            "last_reviewed_at": None,
        }
        conn.execute.return_value.fetchone.return_value = mock_row

        result = dao.find_group("INC-99")
        assert result is not None
        assert result.anchor_entity_id == "INC-99"
        assert result.id == 1

    def test_returns_none_when_not_found(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None

        result = dao.find_group("INC-MISSING")
        assert result is None

    def test_returns_none_on_exception(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        result = dao.find_group("INC-1")
        assert result is None

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None

        dao.find_group("INC-1")
        metrics.start_query.assert_called_once_with(
            "select", "reliability_correlation_groups"
        )
        mock_record.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        dao.find_group("INC-1")
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)

    def test_handles_metrics_recording_failure(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("metrics fail"))
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        result = dao.find_group("INC-1")
        assert result is None


class TestInsertOrUpdateGroup:
    """Tests for insert_or_update_group."""

    def test_inserts_when_no_existing(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        """Calls _insert_group when find_group returns None."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        # find_group returns None (first call = select), then insert succeeds
        conn.execute.return_value.fetchone.return_value = None
        mock_insert_result = MagicMock()
        mock_insert_result.lastrowid = 42
        conn.execute.return_value = mock_insert_result

        group = _make_group()
        result = dao.insert_or_update_group(group)
        # The insert returns lastrowid
        assert result is not None

    def test_updates_when_existing(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        """Calls _update_group when find_group returns a group."""
        # Mock find_group to return existing
        with patch.object(dao, "find_group") as mock_find:
            existing = _make_group(id=10)
            mock_find.return_value = existing

            group = _make_group()
            dao.insert_or_update_group(group)

            # ID should be set from existing
            assert group.id == 10


class TestInsertGroup:
    """Tests for _insert_group."""

    def test_returns_lastrowid_on_success(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.lastrowid = 42
        conn.execute.return_value = mock_result

        group = _make_group()
        result = dao._insert_group(group)
        assert result == 42
        conn.commit.assert_called_once()

    def test_returns_none_on_exception(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        group = _make_group()
        result = dao._insert_group(group)
        assert result is None

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.lastrowid = 1
        conn.execute.return_value = mock_result

        group = _make_group()
        dao._insert_group(group)
        metrics.start_query.assert_called_once_with(
            "insert", "reliability_correlation_groups"
        )
        mock_record.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        group = _make_group()
        dao._insert_group(group)
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)


class TestUpdateGroup:
    """Tests for _update_group."""

    def test_returns_group_id_on_success(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        group = _make_group(id=10)
        result = dao._update_group(group)
        assert result == 10
        conn.commit.assert_called_once()

    def test_returns_none_on_exception(
        self, dao: ReliabilityCorrelationGroupDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        group = _make_group(id=10)
        result = dao._update_group(group)
        assert result is None

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        group = _make_group(id=10)
        dao._update_group(group)
        metrics.start_query.assert_called_once_with(
            "update", "reliability_correlation_groups"
        )
        mock_record.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationGroupDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        group = _make_group(id=10)
        dao._update_group(group)
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)
