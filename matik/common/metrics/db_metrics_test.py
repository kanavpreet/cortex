"""Unit tests for DBMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.db_metrics import DB_LATENCY_BUCKETS, DBMetrics, PoolStats


class TestDBMetricsConstants:
    """Test module constants."""

    def test_latency_buckets(self) -> None:
        """Test DB latency buckets are defined correctly."""
        assert DB_LATENCY_BUCKETS == (
            0.001,
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(DB_LATENCY_BUCKETS) - 1):
            assert DB_LATENCY_BUCKETS[i] < DB_LATENCY_BUCKETS[i + 1]


class TestPoolStats:
    """Test PoolStats dataclass."""

    def test_create_pool_stats(self) -> None:
        """Test creating PoolStats."""
        stats = PoolStats(
            open_connections=10,
            idle_connections=5,
            in_use_connections=5,
        )
        assert stats.open_connections == 10
        assert stats.idle_connections == 5
        assert stats.in_use_connections == 5


class TestDBMetrics:
    """Test suite for DBMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        DBMetrics(meter, "historian")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_db_queries_total" in counter_calls
        assert "matik_db_query_errors_total" in counter_calls
        assert "matik_db_pool_wait_total" in counter_calls

        # Verify histograms created
        histogram_calls = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_db_query_duration_seconds" in histogram_calls
        assert "matik_db_pool_wait_duration_seconds" in histogram_calls

        # Verify gauges created
        gauge_calls = [c[1]["name"] for c in meter.create_gauge.call_args_list]
        assert "matik_db_pool_open_connections" in gauge_calls
        assert "matik_db_pool_idle_connections" in gauge_calls
        assert "matik_db_pool_in_use_connections" in gauge_calls

    def test_record_query_success(self) -> None:
        """Test recording a successful query."""
        meter = self._create_mock_meter()
        query_count = MagicMock()
        query_duration = MagicMock()
        query_errors = MagicMock()

        meter.create_counter.side_effect = [
            query_count,
            query_errors,
            MagicMock(),
        ]
        meter.create_histogram.side_effect = [
            query_duration,
            MagicMock(),
        ]

        metrics = DBMetrics(meter, "historian")
        metrics.record_query(
            operation="select",
            table="incidents",
            duration_seconds=0.05,
        )

        # Verify query count incremented
        query_count.add.assert_called_once()
        call_args = query_count.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["operation"] == "select"
        assert attrs["table"] == "incidents"
        assert attrs["status"] == "success"

        # Verify duration recorded
        query_duration.record.assert_called_once()
        assert query_duration.record.call_args[0][0] == 0.05

        # Verify errors not incremented
        query_errors.add.assert_not_called()

    def test_record_query_error(self) -> None:
        """Test recording a query with error."""
        meter = self._create_mock_meter()
        query_count = MagicMock()
        query_errors = MagicMock()

        meter.create_counter.side_effect = [
            query_count,
            query_errors,
            MagicMock(),
        ]

        metrics = DBMetrics(meter, "historian")
        metrics.record_query(
            operation="insert",
            table="incidents",
            duration_seconds=0.01,
            error=Exception("Duplicate key error"),
        )

        # Verify error count incremented
        query_errors.add.assert_called_once()
        attrs = query_count.add.call_args[0][1]
        assert attrs["status"] == "error"

    def test_update_pool_stats(self) -> None:
        """Test updating pool statistics."""
        meter = self._create_mock_meter()
        open_conns = MagicMock()
        idle_conns = MagicMock()
        in_use_conns = MagicMock()

        meter.create_gauge.side_effect = [
            open_conns,
            idle_conns,
            in_use_conns,
        ]

        metrics = DBMetrics(meter, "api")
        metrics.update_pool_stats(
            PoolStats(
                open_connections=20,
                idle_connections=8,
                in_use_connections=12,
            )
        )

        # Verify gauges set correctly
        open_conns.set.assert_called_once_with(20, {"service": "api"})
        idle_conns.set.assert_called_once_with(8, {"service": "api"})
        in_use_conns.set.assert_called_once_with(12, {"service": "api"})

    def test_record_pool_wait(self) -> None:
        """Test recording pool wait time."""
        meter = self._create_mock_meter()
        wait_count = MagicMock()
        wait_duration = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            wait_count,
        ]
        meter.create_histogram.side_effect = [
            MagicMock(),
            wait_duration,
        ]

        metrics = DBMetrics(meter, "historian")
        metrics.record_pool_wait(0.1)

        wait_count.add.assert_called_once()
        assert wait_count.add.call_args[0][0] == 1
        assert wait_count.add.call_args[0][1] == {"service": "historian"}

        wait_duration.record.assert_called_once()
        assert wait_duration.record.call_args[0][0] == 0.1

    def test_start_query_timing(self) -> None:
        """Test that start_query measures duration correctly."""
        meter = self._create_mock_meter()
        query_duration = MagicMock()
        meter.create_histogram.side_effect = [
            query_duration,
            MagicMock(),
        ]

        metrics = DBMetrics(meter, "historian")

        # Start query
        record = metrics.start_query("select", "incidents")

        # Simulate some work
        time.sleep(0.05)

        # Record completion
        record(None)

        # Verify duration is recorded and is at least 50ms
        query_duration.record.assert_called_once()
        recorded_duration = query_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_query_with_error(self) -> None:
        """Test start_query records errors correctly."""
        meter = self._create_mock_meter()
        query_errors = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            query_errors,
            MagicMock(),
        ]

        metrics = DBMetrics(meter, "api")
        record = metrics.start_query("insert", "users")
        record(Exception("Constraint violation"))

        query_errors.add.assert_called_once()


class TestDBMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        DBMetrics(meter, "historian")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert (
            "Total number of database queries"
            in counter_calls["matik_db_queries_total"]["description"]
        )
        assert counter_calls["matik_db_queries_total"]["unit"] == "{query}"

    def test_histogram_descriptions(self) -> None:
        """Test that histograms have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        DBMetrics(meter, "historian")

        histogram_calls = {
            c[1]["name"]: c[1] for c in meter.create_histogram.call_args_list
        }

        assert histogram_calls["matik_db_query_duration_seconds"]["unit"] == "s"
        assert (
            histogram_calls["matik_db_query_duration_seconds"][
                "explicit_bucket_boundaries_advisory"
            ]
            == DB_LATENCY_BUCKETS
        )

    def test_gauge_descriptions(self) -> None:
        """Test that gauges have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        DBMetrics(meter, "historian")

        gauge_calls = {c[1]["name"]: c[1] for c in meter.create_gauge.call_args_list}

        assert gauge_calls["matik_db_pool_open_connections"]["unit"] == "{connection}"
        assert gauge_calls["matik_db_pool_idle_connections"]["unit"] == "{connection}"
        assert gauge_calls["matik_db_pool_in_use_connections"]["unit"] == "{connection}"
