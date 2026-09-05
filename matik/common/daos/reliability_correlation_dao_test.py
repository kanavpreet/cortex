"""Tests for ReliabilityCorrelationDAO."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from common.daos.reliability_correlation_dao import (
    BATCH_SIZE,
    ReliabilityCorrelationDAO,
    _to_json,
)
from common.models.reliability_correlation import ReliabilityCorrelation


class TestToJson:
    """Tests for _to_json helper."""

    def test_none_returns_none(self) -> None:
        assert _to_json(None) is None

    def test_list_returns_json_string(self) -> None:
        assert _to_json(["svc-a", "svc-b"]) == '["svc-a", "svc-b"]'

    def test_empty_list_returns_json_string(self) -> None:
        assert _to_json([]) == "[]"


class TestReliabilityCorrelationDAOInit:
    """Tests for DAO constructor."""

    def test_stores_engine_and_metrics(self) -> None:
        engine = MagicMock()
        metrics = MagicMock()
        dao = ReliabilityCorrelationDAO(engine, metrics)
        assert dao._engine is engine
        assert dao._metrics is metrics

    def test_metrics_default_none(self) -> None:
        engine = MagicMock()
        dao = ReliabilityCorrelationDAO(engine)
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
def dao(mock_engine: MagicMock) -> ReliabilityCorrelationDAO:
    """Create DAO with mock engine."""
    return ReliabilityCorrelationDAO(mock_engine)


def _make_correlation(**overrides: Any) -> ReliabilityCorrelation:
    """Build a minimal ReliabilityCorrelation."""
    defaults: dict[str, Any] = {
        "anchor_entity_id": "INC-99",
        "correlation_type": "SERVICE_MATCH",
        "entity_type": "github_pr",
        "entity_id": "PR-1",
    }
    defaults.update(overrides)
    return ReliabilityCorrelation(**defaults)


class TestFindByAnchor:
    """Tests for find_by_anchor."""

    def test_returns_list_on_success(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        """Rows returned by DB are converted to models."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_row = MagicMock()
        # model_validate with from_attributes reads attributes directly
        mock_row.id = 1
        mock_row.anchor_entity_id = "INC-99"
        mock_row.correlation_type = "SERVICE_MATCH"
        mock_row.entity_type = "github_pr"
        mock_row.entity_id = "PR-1"
        mock_row.services = None
        mock_row.start_time = None
        mock_row.end_time = None
        mock_row.reasoning = None
        mock_row.base_score = 0.8
        mock_row.final_score = 0.64
        mock_row.scoring_version = "incident_v1"
        conn.execute.return_value.fetchall.return_value = [mock_row]

        results = dao.find_by_anchor("INC-99")
        assert len(results) == 1
        assert results[0].anchor_entity_id == "INC-99"
        assert results[0].base_score == 0.8

    def test_returns_empty_list_on_no_rows(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []
        results = dao.find_by_anchor("INC-1")
        assert results == []

    def test_returns_empty_list_on_exception(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")
        results = dao.find_by_anchor("INC-1")
        assert results == []

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationDAO(mock_engine, metrics)
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = []

        dao.find_by_anchor("INC-1")
        metrics.start_query.assert_called_once_with(
            "select", "reliability_correlations"
        )
        mock_record.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationDAO(mock_engine, metrics)
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        dao.find_by_anchor("INC-1")
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)

    def test_handles_metrics_recording_failure(self, mock_engine: MagicMock) -> None:
        """DAO should not raise if metrics recording itself fails."""
        metrics = MagicMock()
        mock_record = MagicMock(side_effect=Exception("metrics fail"))
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationDAO(mock_engine, metrics)
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        results = dao.find_by_anchor("INC-1")
        assert results == []


class TestUpsertCorrelationsBatch:
    """Tests for upsert_correlations_batch."""

    def test_empty_list_returns_zero(self, dao: ReliabilityCorrelationDAO) -> None:
        result = dao.upsert_correlations_batch([])
        assert result == 0

    def test_single_item_upsert(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result

        corr = _make_correlation()
        result = dao.upsert_correlations_batch([corr])
        assert result == 1
        conn.commit.assert_called_once()

    def test_returns_none_on_failure(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        corr = _make_correlation()
        result = dao.upsert_correlations_batch([corr])
        assert result is None

    def test_chunks_by_batch_size(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        """List larger than BATCH_SIZE is chunked."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 500
        conn.execute.return_value = mock_result

        corrs = [_make_correlation(entity_id=f"PR-{i}") for i in range(BATCH_SIZE + 1)]
        result = dao.upsert_correlations_batch(corrs)
        # Two batches: BATCH_SIZE + 1
        assert conn.execute.call_count == 2
        assert result == 1000  # 500 per batch

    def test_partial_failure_returns_none(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        """If second chunk fails, returns None."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 500
        # First call succeeds, second raises
        conn.execute.side_effect = [mock_result, Exception("DB error")]

        corrs = [_make_correlation(entity_id=f"PR-{i}") for i in range(BATCH_SIZE + 1)]
        result = dao.upsert_correlations_batch(corrs)
        assert result is None

    def test_records_metrics_on_success(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result

        corr = _make_correlation()
        dao.upsert_correlations_batch([corr])
        metrics.start_query.assert_called_once_with(
            "upsert", "reliability_correlations"
        )
        mock_record.assert_called_once_with(None)

    def test_records_metrics_on_error(self, mock_engine: MagicMock) -> None:
        metrics = MagicMock()
        mock_record = MagicMock()
        metrics.start_query.return_value = mock_record
        dao = ReliabilityCorrelationDAO(mock_engine, metrics)

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = Exception("DB error")

        corr = _make_correlation()
        dao.upsert_correlations_batch([corr])
        mock_record.assert_called_once()
        assert isinstance(mock_record.call_args[0][0], Exception)


class TestUpsertBatch:
    """Tests for _upsert_batch internal method."""

    def test_empty_list_returns_zero(self, dao: ReliabilityCorrelationDAO) -> None:
        result = dao._upsert_batch([])
        assert result == 0

    def test_serializes_services_to_json(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        """Services list is serialized via _to_json."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result

        corr = _make_correlation(services=["svc-a"])
        dao._upsert_batch([corr])

        # Verify the params include serialized JSON
        call_args = conn.execute.call_args
        params = call_args[0][1]
        assert params["services_0"] == '["svc-a"]'

    def test_with_scoring_fields(
        self, dao: ReliabilityCorrelationDAO, mock_engine: MagicMock
    ) -> None:
        conn = mock_engine.connect.return_value.__enter__.return_value
        mock_result = MagicMock()
        mock_result.rowcount = 1
        conn.execute.return_value = mock_result

        corr = _make_correlation(
            base_score=0.8,
            final_score=0.64,
            scoring_version="incident_v1",
        )
        dao._upsert_batch([corr])

        call_args = conn.execute.call_args
        params = call_args[0][1]
        assert params["base_score_0"] == 0.8
        assert params["final_score_0"] == 0.64
        assert params["scoring_version_0"] == "incident_v1"
