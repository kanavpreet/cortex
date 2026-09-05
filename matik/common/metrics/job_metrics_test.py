"""Unit tests for JobMetrics."""

import time
from unittest.mock import MagicMock, patch

from common.metrics.job_metrics import JOB_DURATION_BUCKETS, JobMetrics


class TestJobMetricsConstants:
    """Test module constants."""

    def test_duration_buckets(self) -> None:
        """Test job duration buckets are defined correctly."""
        assert JOB_DURATION_BUCKETS == (
            1.0,
            5.0,
            10.0,
            30.0,
            60.0,
            120.0,
            300.0,
            600.0,
            1800.0,
            3600.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(JOB_DURATION_BUCKETS) - 1):
            assert JOB_DURATION_BUCKETS[i] < JOB_DURATION_BUCKETS[i + 1]


class TestJobMetrics:
    """Test suite for JobMetrics."""

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
        JobMetrics(meter, "historian")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_job_executions_total" in counter_calls
        assert "matik_job_errors_total" in counter_calls
        assert "matik_job_errors_encountered_total" in counter_calls
        assert "matik_job_items_processed_total" in counter_calls

        # Verify histogram created
        meter.create_histogram.assert_called_once()
        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_job_duration_seconds"

        # Verify gauge created
        meter.create_gauge.assert_called_once()
        gauge_call = meter.create_gauge.call_args[1]
        assert gauge_call["name"] == "matik_job_last_run_timestamp"

    def test_record_job_execution_success(self) -> None:
        """Test recording a successful job execution."""
        meter = self._create_mock_meter()
        job_executions = MagicMock()
        job_errors = MagicMock()
        job_errors_encountered = MagicMock()
        items_processed = MagicMock()
        job_duration = MagicMock()
        last_run_timestamp = MagicMock()

        meter.create_counter.side_effect = [
            job_executions,
            job_errors,
            job_errors_encountered,
            items_processed,
        ]
        meter.create_histogram.return_value = job_duration
        meter.create_gauge.return_value = last_run_timestamp

        metrics = JobMetrics(meter, "historian")
        metrics.record_job_execution(
            connector_type="incidentio_incidents",
            duration_seconds=120.0,
            item_count=42,
        )

        # Verify job execution count incremented
        job_executions.add.assert_called_once()
        call_args = job_executions.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["connector_type"] == "incidentio_incidents"
        assert attrs["status"] == "success"

        # Verify duration recorded
        job_duration.record.assert_called_once()
        assert job_duration.record.call_args[0][0] == 120.0

        # Verify items processed
        items_processed.add.assert_called_once()
        assert items_processed.add.call_args[0][0] == 42

        # Verify last run timestamp
        last_run_timestamp.set.assert_called_once()

        # Verify errors not incremented
        job_errors.add.assert_not_called()

    def test_record_job_execution_error(self) -> None:
        """Test recording a failed job execution."""
        meter = self._create_mock_meter()
        job_executions = MagicMock()
        job_errors = MagicMock()

        meter.create_counter.side_effect = [
            job_executions,
            job_errors,
            MagicMock(),
            MagicMock(),
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_job_execution(
            connector_type="jira_issues",
            duration_seconds=5.0,
            item_count=0,
            error=Exception("Connection timeout"),
        )

        # Verify error recorded
        job_errors.add.assert_called_once()
        attrs = job_executions.add.call_args[0][1]
        assert attrs["status"] == "error"

    def test_record_job_execution_no_items(self) -> None:
        """Test recording job execution with no items processed still emits the counter."""
        meter = self._create_mock_meter()
        items_processed = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            items_processed,
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_job_execution(
            connector_type="incidentio_incidents",
            duration_seconds=1.0,
            item_count=0,
        )

        # Verify items_processed is called even with 0 items (registers time series)
        items_processed.add.assert_called_once()
        assert items_processed.add.call_args[0][0] == 0

    def test_record_items_processed(self) -> None:
        """Test recording items processed separately."""
        meter = self._create_mock_meter()
        items_processed = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            items_processed,
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_items_processed("pagerduty_alerts", 100)

        items_processed.add.assert_called_once()
        call_args = items_processed.add.call_args
        assert call_args[0][0] == 100
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["connector_type"] == "pagerduty_alerts"

    def test_record_items_processed_zero_count(self) -> None:
        """Test that zero count does not record."""
        meter = self._create_mock_meter()
        items_processed = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            items_processed,
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_items_processed("incidentio_incidents", 0)

        items_processed.add.assert_not_called()

    def test_record_items_processed_negative_count(self) -> None:
        """Test that negative count does not record."""
        meter = self._create_mock_meter()
        items_processed = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            items_processed,
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_items_processed("incidentio_incidents", -5)

        items_processed.add.assert_not_called()

    def test_record_job_error(self) -> None:
        """Test recording non-fatal job errors."""
        meter = self._create_mock_meter()
        job_errors_encountered = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            job_errors_encountered,
            MagicMock(),
        ]

        metrics = JobMetrics(meter, "historian")
        metrics.record_job_error("incidentio_incidents", "rate_limit")

        job_errors_encountered.add.assert_called_once()
        call_args = job_errors_encountered.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["connector_type"] == "incidentio_incidents"
        assert attrs["error_type"] == "rate_limit"

    def test_start_job_timing(self) -> None:
        """Test that start_job measures duration correctly."""
        meter = self._create_mock_meter()
        job_duration = MagicMock()
        meter.create_histogram.return_value = job_duration

        metrics = JobMetrics(meter, "historian")

        # Start job
        record = metrics.start_job("incidentio_incidents")

        # Simulate some work
        time.sleep(0.05)

        # Record completion
        record(10, None)

        # Verify duration is recorded and is at least 50ms
        job_duration.record.assert_called_once()
        recorded_duration = job_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_job_with_error(self) -> None:
        """Test start_job records errors correctly."""
        meter = self._create_mock_meter()
        job_errors = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            job_errors,
            MagicMock(),
            MagicMock(),
        ]

        metrics = JobMetrics(meter, "chronicler")
        record = metrics.start_job("webhook_processor")
        record(0, RuntimeError("Processing failed"))

        job_errors.add.assert_called_once()

    @patch("time.time")
    def test_last_run_timestamp_recorded(self, mock_time: MagicMock) -> None:
        """Test that last run timestamp is recorded correctly."""
        mock_time.return_value = 1704067200  # 2024-01-01 00:00:00 UTC

        meter = self._create_mock_meter()
        last_run_timestamp = MagicMock()
        meter.create_gauge.return_value = last_run_timestamp

        metrics = JobMetrics(meter, "historian")
        metrics.record_job_execution(
            connector_type="incidentio_incidents",
            duration_seconds=60.0,
        )

        last_run_timestamp.set.assert_called_once()
        call_args = last_run_timestamp.set.call_args
        assert call_args[0][0] == 1704067200
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["connector_type"] == "incidentio_incidents"


class TestJobMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        JobMetrics(meter, "historian")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert (
            "Total number of background job executions"
            in counter_calls["matik_job_executions_total"]["description"]
        )
        assert counter_calls["matik_job_executions_total"]["unit"] == "{execution}"

        assert (
            "Total number of background job errors"
            in counter_calls["matik_job_errors_total"]["description"]
        )
        assert counter_calls["matik_job_errors_total"]["unit"] == "{error}"

        assert (
            "errors encountered during job"
            in counter_calls["matik_job_errors_encountered_total"]["description"]
        )
        assert counter_calls["matik_job_errors_encountered_total"]["unit"] == "{error}"

        assert (
            "Total number of items processed"
            in counter_calls["matik_job_items_processed_total"]["description"]
        )
        assert counter_calls["matik_job_items_processed_total"]["unit"] == "{item}"

    def test_histogram_description(self) -> None:
        """Test that histogram has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        JobMetrics(meter, "historian")

        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_job_duration_seconds"
        assert "duration" in histogram_call["description"].lower()
        assert histogram_call["unit"] == "s"
        assert (
            histogram_call["explicit_bucket_boundaries_advisory"]
            == JOB_DURATION_BUCKETS
        )

    def test_gauge_description(self) -> None:
        """Test that gauge has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        JobMetrics(meter, "historian")

        gauge_call = meter.create_gauge.call_args[1]
        assert gauge_call["name"] == "matik_job_last_run_timestamp"
        assert "timestamp" in gauge_call["description"].lower()
        assert gauge_call["unit"] == "s"
