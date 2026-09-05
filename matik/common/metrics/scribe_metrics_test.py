"""Unit tests for ScribeMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.scribe_metrics import MESSAGE_PROCESSING_BUCKETS, ScribeMetrics


class TestScribeMetricsConstants:
    """Test module constants."""

    def test_processing_buckets(self) -> None:
        """Test message processing buckets are defined correctly."""
        assert MESSAGE_PROCESSING_BUCKETS == (
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
        for i in range(len(MESSAGE_PROCESSING_BUCKETS) - 1):
            assert MESSAGE_PROCESSING_BUCKETS[i] < MESSAGE_PROCESSING_BUCKETS[i + 1]


class TestScribeMetrics:
    """Test suite for ScribeMetrics."""

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
        ScribeMetrics(meter, "scribe")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_scribe_messages_processed_total" in counter_calls
        assert "matik_scribe_dlq_messages_total" in counter_calls
        assert "matik_scribe_backoff_total" in counter_calls
        assert "matik_scribe_hook_total" in counter_calls

        # Verify histograms created (message processing + hook duration)
        histogram_names = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_scribe_message_processing_duration_seconds" in histogram_names
        assert "matik_scribe_hook_duration_seconds" in histogram_names

        # Verify gauge created
        meter.create_gauge.assert_called_once()
        gauge_call = meter.create_gauge.call_args[1]
        assert gauge_call["name"] == "matik_scribe_queue_depth"

    def test_record_message_processed_success(self) -> None:
        """Test recording a successfully processed message."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()
        message_duration = MagicMock()

        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.side_effect = [message_duration, MagicMock()]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_message_processed(
            source_type="incidentio",
            message_type="base",
            status="success",
            duration_seconds=0.05,
        )

        # Verify counter incremented with correct labels
        messages_processed.add.assert_called_once()
        call_args = messages_processed.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["source_type"] == "incidentio"
        assert attrs["message_type"] == "base"
        assert attrs["status"] == "success"

        # Verify duration recorded with same labels
        message_duration.record.assert_called_once()
        assert message_duration.record.call_args[0][0] == 0.05
        assert message_duration.record.call_args[0][1] == attrs

    def test_record_message_processed_error(self) -> None:
        """Test recording a message that failed processing."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()

        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_message_processed(
            source_type="jira",
            message_type="enrichment",
            status="error",
            duration_seconds=0.01,
        )

        attrs = messages_processed.add.call_args[0][1]
        assert attrs["source_type"] == "jira"
        assert attrs["message_type"] == "enrichment"
        assert attrs["status"] == "error"

    def test_record_message_processed_all_source_types(self) -> None:
        """Test that all expected source types can be recorded."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()
        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")

        for source_type in ("incidentio", "ghe_pr", "jira", "correlation"):
            metrics.record_message_processed(
                source_type=source_type,
                message_type="base",
                status="success",
                duration_seconds=0.01,
            )

        assert messages_processed.add.call_count == 4

    def test_record_dlq_message(self) -> None:
        """Test recording a message sent to the DLQ."""
        meter = self._create_mock_meter()
        dlq_messages = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            dlq_messages,
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_dlq_message("incidentio", "MalformedMessageError")

        dlq_messages.add.assert_called_once()
        call_args = dlq_messages.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "scribe"
        assert attrs["source_type"] == "incidentio"
        assert attrs["error_type"] == "MalformedMessageError"

    def test_record_dlq_message_validation_error(self) -> None:
        """Test recording a DLQ routing due to a validation error."""
        meter = self._create_mock_meter()
        dlq_messages = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            dlq_messages,
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_dlq_message("ghe_pr", "ValidationError")

        attrs = dlq_messages.add.call_args[0][1]
        assert attrs["service"] == "scribe"
        assert attrs["source_type"] == "ghe_pr"
        assert attrs["error_type"] == "ValidationError"

    def test_record_backoff(self) -> None:
        """Test recording a backoff retry."""
        meter = self._create_mock_meter()
        backoff_total = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            backoff_total,
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_backoff("jira")

        backoff_total.add.assert_called_once()
        call_args = backoff_total.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["source_type"] == "jira"

    def test_record_backoff_multiple_times(self) -> None:
        """Test that backoff can be recorded multiple times."""
        meter = self._create_mock_meter()
        backoff_total = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            backoff_total,
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_backoff("incidentio")
        metrics.record_backoff("incidentio")
        metrics.record_backoff("ghe_pr")

        assert backoff_total.add.call_count == 3

    def test_set_queue_depth(self) -> None:
        """Test setting the queue depth gauge."""
        meter = self._create_mock_meter()
        queue_depth = MagicMock()
        meter.create_gauge.return_value = queue_depth

        metrics = ScribeMetrics(meter, "scribe")
        metrics.set_queue_depth("scribe", 42)

        queue_depth.set.assert_called_once_with(42, {"queue": "scribe"})

    def test_set_queue_depth_dlq(self) -> None:
        """Test setting the DLQ depth gauge."""
        meter = self._create_mock_meter()
        queue_depth = MagicMock()
        meter.create_gauge.return_value = queue_depth

        metrics = ScribeMetrics(meter, "scribe")
        metrics.set_queue_depth("scribe-dlq", 0)

        queue_depth.set.assert_called_once_with(0, {"queue": "scribe-dlq"})

    def test_set_queue_depth_multiple_queues(self) -> None:
        """Test that different queues get distinct label values."""
        meter = self._create_mock_meter()
        queue_depth = MagicMock()
        meter.create_gauge.return_value = queue_depth

        metrics = ScribeMetrics(meter, "scribe")
        metrics.set_queue_depth("scribe", 10)
        metrics.set_queue_depth("scribe-dlq", 3)

        assert queue_depth.set.call_count == 2
        first_call_attrs = queue_depth.set.call_args_list[0][0][1]
        second_call_attrs = queue_depth.set.call_args_list[1][0][1]
        assert first_call_attrs["queue"] == "scribe"
        assert second_call_attrs["queue"] == "scribe-dlq"

    def test_start_message_timing(self) -> None:
        """Test that start_message measures duration correctly."""
        meter = self._create_mock_meter()
        message_duration = MagicMock()
        meter.create_histogram.return_value = message_duration

        metrics = ScribeMetrics(meter, "scribe")

        record = metrics.start_message("incidentio", "base")
        time.sleep(0.05)
        record("success")

        message_duration.record.assert_called_once()
        recorded_duration = message_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_message_passes_labels(self) -> None:
        """Test that start_message passes source_type and message_type as labels."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()

        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        record = metrics.start_message("ghe_pr", "enrichment")
        record("success")

        attrs = messages_processed.add.call_args[0][1]
        assert attrs["source_type"] == "ghe_pr"
        assert attrs["message_type"] == "enrichment"
        assert attrs["status"] == "success"

    def test_start_message_error_status(self) -> None:
        """Test that start_message records error status correctly."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()

        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        record = metrics.start_message("jira", "base")
        record("error")

        attrs = messages_processed.add.call_args[0][1]
        assert attrs["status"] == "error"


class TestScribeMetricsBatching:
    """Tests for batching-related metric methods added with the batcher."""

    def _make_metrics(self) -> tuple["ScribeMetrics", MagicMock, MagicMock, MagicMock]:
        meter = MagicMock()
        messages_processed = MagicMock()
        message_duration = MagicMock()
        backoff_total = MagicMock()
        queue_depth = MagicMock()
        hook_total = MagicMock()
        hook_duration = MagicMock()
        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),  # dlq
            backoff_total,
            hook_total,
        ]
        meter.create_histogram.side_effect = [message_duration, hook_duration]
        meter.create_gauge.return_value = queue_depth
        metrics = ScribeMetrics(meter, "scribe")
        return metrics, messages_processed, message_duration, backoff_total

    def test_record_hook_increments_counter_and_histogram(self) -> None:
        meter = MagicMock()
        hook_total = MagicMock()
        hook_duration = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            hook_total,
        ]
        meter.create_histogram.side_effect = [MagicMock(), hook_duration]
        meter.create_gauge.return_value = MagicMock()

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_hook("enigmatologist_correlation", "success", 0.03)

        hook_total.add.assert_called_once_with(
            1,
            {
                "service": "scribe",
                "hook_name": "enigmatologist_correlation",
                "status": "success",
            },
        )
        hook_duration.record.assert_called_once_with(
            0.03,
            {
                "service": "scribe",
                "hook_name": "enigmatologist_correlation",
                "status": "success",
            },
        )

    def test_record_batch_flush_uses_batch_size(self) -> None:
        metrics, messages_processed, message_duration, _ = self._make_metrics()
        metrics.record_batch_flush("incidentio", "base", 25, 0.1, "success")

        call = messages_processed.add.call_args
        assert call[0][0] == 25
        attrs = call[0][1]
        assert attrs["source_type"] == "incidentio"
        assert attrs["message_type"] == "base"
        assert attrs["status"] == "success"
        message_duration.record.assert_called_once_with(0.1, attrs)

    def test_record_buffer_size_sets_gauge(self) -> None:
        meter = MagicMock()
        queue_depth = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = queue_depth

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_buffer_size("jira", 42)

        queue_depth.set.assert_called_once_with(42, {"source_type": "jira"})

    def test_record_group_failure_increments_backoff_by_size(self) -> None:
        metrics, _, _, backoff_total = self._make_metrics()
        metrics.record_group_failure("incidentio", "base", 10)

        call = backoff_total.add.call_args
        assert call[0][0] == 10
        attrs = call[0][1]
        assert attrs["source_type"] == "incidentio"
        assert attrs["message_type"] == "base"
        assert attrs["redriven"] == "false"

    def test_record_batch_flush_defaults_redriven_false(self) -> None:
        metrics, messages_processed, _, _ = self._make_metrics()
        metrics.record_batch_flush("incidentio", "base", 25, 0.1, "success")

        attrs = messages_processed.add.call_args[0][1]
        assert attrs["redriven"] == "false"

    def test_record_batch_flush_redriven_true(self) -> None:
        metrics, messages_processed, _, _ = self._make_metrics()
        metrics.record_batch_flush(
            "incidentio", "base", 25, 0.1, "success", redriven=True
        )

        attrs = messages_processed.add.call_args[0][1]
        assert attrs["redriven"] == "true"

    def test_record_group_failure_redriven_true(self) -> None:
        metrics, _, _, backoff_total = self._make_metrics()
        metrics.record_group_failure("incidentio", "base", 10, redriven=True)

        attrs = backoff_total.add.call_args[0][1]
        assert attrs["redriven"] == "true"

    def test_record_dlq_message_redriven_true(self) -> None:
        meter = MagicMock()
        dlq_messages = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            dlq_messages,
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_dlq_message("incidentio", "RetryableError", redriven=True)

        attrs = dlq_messages.add.call_args[0][1]
        assert attrs["redriven"] == "true"

    def test_record_dlq_message_defaults_redriven_false(self) -> None:
        meter = MagicMock()
        dlq_messages = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            dlq_messages,
            MagicMock(),
            MagicMock(),
        ]

        metrics = ScribeMetrics(meter, "scribe")
        metrics.record_dlq_message("incidentio", "MalformedMessageError")

        attrs = dlq_messages.add.call_args[0][1]
        assert attrs["redriven"] == "false"


class TestScribeMetricsInstrumentDescriptions:
    """Test instrument names, descriptions, and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper names, descriptions, and units."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        ScribeMetrics(meter, "scribe")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert "matik_scribe_messages_processed_total" in counter_calls
        assert (
            counter_calls["matik_scribe_messages_processed_total"]["unit"]
            == "{message}"
        )
        assert (
            "processed"
            in counter_calls["matik_scribe_messages_processed_total"][
                "description"
            ].lower()
        )

        assert "matik_scribe_dlq_messages_total" in counter_calls
        assert counter_calls["matik_scribe_dlq_messages_total"]["unit"] == "{message}"
        assert (
            "dead letter"
            in counter_calls["matik_scribe_dlq_messages_total"]["description"].lower()
        )

        assert "matik_scribe_backoff_total" in counter_calls
        assert counter_calls["matik_scribe_backoff_total"]["unit"] == "{retry}"
        assert (
            "backoff"
            in counter_calls["matik_scribe_backoff_total"]["description"].lower()
        )

    def test_histogram_description(self) -> None:
        """Test that histograms have proper names, descriptions, units, and buckets."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        ScribeMetrics(meter, "scribe")

        histogram_calls = {
            c[1]["name"]: c[1] for c in meter.create_histogram.call_args_list
        }

        msg_call = histogram_calls["matik_scribe_message_processing_duration_seconds"]
        assert "process" in msg_call["description"].lower()
        assert msg_call["unit"] == "s"
        assert (
            msg_call["explicit_bucket_boundaries_advisory"]
            == MESSAGE_PROCESSING_BUCKETS
        )

        hook_call = histogram_calls["matik_scribe_hook_duration_seconds"]
        assert "hook" in hook_call["description"].lower()
        assert hook_call["unit"] == "s"
        assert (
            hook_call["explicit_bucket_boundaries_advisory"]
            == MESSAGE_PROCESSING_BUCKETS
        )

    def test_gauge_description(self) -> None:
        """Test that gauge has proper name, description, and unit."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_gauge.return_value = MagicMock()

        ScribeMetrics(meter, "scribe")

        gauge_call = meter.create_gauge.call_args[1]
        assert gauge_call["name"] == "matik_scribe_queue_depth"
        assert "queue" in gauge_call["description"].lower()
        assert gauge_call["unit"] == "{message}"
