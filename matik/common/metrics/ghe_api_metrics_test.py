"""Unit tests for GHEAPIMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.ghe_api_metrics import (
    API_LATENCY_BUCKETS,
    BATCH_SIZE_BUCKETS,
    GHEAPIMetrics,
)


class TestGHEAPIMetricsConstants:
    """Test module constants."""

    def test_api_latency_buckets(self) -> None:
        """Test API latency buckets are defined correctly."""
        assert API_LATENCY_BUCKETS == (
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(API_LATENCY_BUCKETS) - 1):
            assert API_LATENCY_BUCKETS[i] < API_LATENCY_BUCKETS[i + 1]

    def test_batch_size_buckets(self) -> None:
        """Test batch size buckets are defined correctly."""
        assert BATCH_SIZE_BUCKETS == (
            1,
            5,
            10,
            25,
            50,
            100,
            250,
            500,
            1000,
        )
        # Verify buckets are in ascending order
        for i in range(len(BATCH_SIZE_BUCKETS) - 1):
            assert BATCH_SIZE_BUCKETS[i] < BATCH_SIZE_BUCKETS[i + 1]


class TestGHEAPIMetrics:
    """Test suite for GHEAPIMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        GHEAPIMetrics(meter, "api")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_ghe_api_operations_total" in counter_calls
        assert "matik_ghe_api_entities_affected_total" in counter_calls

        # Verify histograms created
        histogram_calls = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_ghe_api_operation_duration_seconds" in histogram_calls
        assert "matik_ghe_api_batch_size" in histogram_calls

    def test_record_operation_success(self) -> None:
        """Test recording a successful operation."""
        meter = self._create_mock_meter()
        operation_count = MagicMock()
        operation_duration = MagicMock()

        meter.create_counter.side_effect = [operation_count, MagicMock()]
        meter.create_histogram.side_effect = [operation_duration, MagicMock()]

        metrics = GHEAPIMetrics(meter, "api")
        metrics.record_operation(
            operation="upsert",
            entity_type="organization",
            success=True,
            duration_seconds=0.5,
        )

        # Verify operation count incremented
        operation_count.add.assert_called_once()
        call_args = operation_count.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "api"
        assert attrs["operation"] == "upsert"
        assert attrs["entity_type"] == "organization"
        assert attrs["status"] == "success"

        # Verify duration recorded
        operation_duration.record.assert_called_once()
        assert operation_duration.record.call_args[0][0] == 0.5

    def test_record_operation_error(self) -> None:
        """Test recording a failed operation."""
        meter = self._create_mock_meter()
        operation_count = MagicMock()

        meter.create_counter.side_effect = [operation_count, MagicMock()]

        metrics = GHEAPIMetrics(meter, "api")
        metrics.record_operation(
            operation="upsert",
            entity_type="repository",
            success=False,
            duration_seconds=1.0,
        )

        # Verify status is error
        call_args = operation_count.add.call_args
        attrs = call_args[0][1]
        assert attrs["status"] == "error"

    def test_record_batch_operation_success(self) -> None:
        """Test recording a successful batch operation.

        Note: Duration and operation count are tracked via start_operation(),
        so record_batch_operation only records batch-specific metrics.
        """
        meter = self._create_mock_meter()
        entities_affected = MagicMock()
        batch_size_histogram = MagicMock()

        meter.create_counter.side_effect = [MagicMock(), entities_affected]
        meter.create_histogram.side_effect = [MagicMock(), batch_size_histogram]

        metrics = GHEAPIMetrics(meter, "api")
        metrics.record_batch_operation(
            entity_type="pull_request",
            batch_size=100,
            affected_rows=98,
            success=True,
        )

        # Verify batch size recorded
        batch_size_histogram.record.assert_called_once()
        assert batch_size_histogram.record.call_args[0][0] == 100

        # Verify entities affected incremented
        entities_affected.add.assert_called_once()
        affected_call = entities_affected.add.call_args
        assert affected_call[0][0] == 98
        affected_attrs = affected_call[0][1]
        assert affected_attrs["entity_type"] == "pull_request"
        assert affected_attrs["operation"] == "batch_upsert"

    def test_record_batch_operation_failure_no_entities_affected(self) -> None:
        """Test that failed batch operations don't record entities affected."""
        meter = self._create_mock_meter()
        entities_affected = MagicMock()

        meter.create_counter.side_effect = [MagicMock(), entities_affected]

        metrics = GHEAPIMetrics(meter, "api")
        metrics.record_batch_operation(
            entity_type="pull_request",
            batch_size=100,
            affected_rows=0,
            success=False,
        )

        # Verify entities affected NOT incremented on failure
        entities_affected.add.assert_not_called()

    def test_start_operation_timing(self) -> None:
        """Test that start_operation measures duration correctly."""
        meter = self._create_mock_meter()
        operation_duration = MagicMock()
        meter.create_histogram.side_effect = [operation_duration, MagicMock()]

        metrics = GHEAPIMetrics(meter, "api")

        # Start operation
        record = metrics.start_operation("upsert", "organization")

        # Simulate some work
        time.sleep(0.05)

        # Record completion
        record(True)

        # Verify duration is recorded and is at least 50ms
        operation_duration.record.assert_called_once()
        recorded_duration = operation_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_operation_records_failure(self) -> None:
        """Test that start_operation records failure correctly."""
        meter = self._create_mock_meter()
        operation_count = MagicMock()
        meter.create_counter.side_effect = [operation_count, MagicMock()]

        metrics = GHEAPIMetrics(meter, "api")

        record = metrics.start_operation("upsert", "repository")
        record(False)

        # Verify status is error
        attrs = operation_count.add.call_args[0][1]
        assert attrs["status"] == "error"


class TestGHEAPIMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        GHEAPIMetrics(meter, "api")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        # Operations counter
        assert (
            "Total number of GHE API operations"
            in counter_calls["matik_ghe_api_operations_total"]["description"]
        )
        assert counter_calls["matik_ghe_api_operations_total"]["unit"] == "{operation}"

        # Entities affected counter
        assert (
            "Total number of entities affected"
            in counter_calls["matik_ghe_api_entities_affected_total"]["description"]
        )
        assert (
            counter_calls["matik_ghe_api_entities_affected_total"]["unit"] == "{entity}"
        )

    def test_histogram_descriptions(self) -> None:
        """Test that histograms have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        GHEAPIMetrics(meter, "api")

        histogram_calls = {
            c[1]["name"]: c[1] for c in meter.create_histogram.call_args_list
        }

        # Duration histogram
        duration_call = histogram_calls["matik_ghe_api_operation_duration_seconds"]
        assert "duration" in duration_call["description"].lower()
        assert duration_call["unit"] == "s"
        assert (
            duration_call["explicit_bucket_boundaries_advisory"] == API_LATENCY_BUCKETS
        )

        # Batch size histogram
        batch_call = histogram_calls["matik_ghe_api_batch_size"]
        assert "batch size" in batch_call["description"].lower()
        assert batch_call["unit"] == "{item}"
        assert batch_call["explicit_bucket_boundaries_advisory"] == BATCH_SIZE_BUCKETS
