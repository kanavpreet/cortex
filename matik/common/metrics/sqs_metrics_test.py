"""Unit tests for SQSMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.sqs_metrics import SQS_PROCESSING_DURATION_BUCKETS, SQSMetrics


class TestSQSMetricsConstants:
    """Test module constants."""

    def test_processing_duration_buckets(self) -> None:
        """Processing duration buckets are defined correctly."""
        assert SQS_PROCESSING_DURATION_BUCKETS == (
            0.5,
            1.0,
            5.0,
            10.0,
            30.0,
            60.0,
            120.0,
            300.0,
            600.0,
        )
        for i in range(len(SQS_PROCESSING_DURATION_BUCKETS) - 1):
            assert (
                SQS_PROCESSING_DURATION_BUCKETS[i]
                < SQS_PROCESSING_DURATION_BUCKETS[i + 1]
            )


class TestSQSMetrics:
    """Test suite for SQSMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Init creates all required instruments."""
        meter = self._create_mock_meter()
        SQSMetrics(meter, "enigmatologist")

        counter_names = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_sqs_poll_total" in counter_names
        assert "matik_sqs_processed_messages_total" in counter_names
        assert "matik_sqs_failed_messages_total" in counter_names
        assert "matik_sqs_message_retries_total" in counter_names

        histogram_names = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_sqs_processing_seconds" in histogram_names

    def test_record_poll(self) -> None:
        """record_poll increments poll counter with queue_name and service labels."""
        meter = self._create_mock_meter()
        poll_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            poll_mock,
            MagicMock(),
        ]

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_poll("matik-enig-triggers-sandbox-queue")

        poll_mock.add.assert_called_once()
        value, attrs = poll_mock.add.call_args[0]
        assert value == 1
        assert attrs["service"] == "enigmatologist"
        assert attrs["queue_name"] == "matik-enig-triggers-sandbox-queue"

    def test_record_retry_redelivery(self) -> None:
        """receive_count > 1 records a retry with queue_name and service labels."""
        meter = self._create_mock_meter()
        retry_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            retry_mock,
        ]

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_retry("matik-enig-triggers-sandbox-queue", 3)

        retry_mock.add.assert_called_once()
        value, attrs = retry_mock.add.call_args[0]
        assert value == 1
        assert attrs["service"] == "enigmatologist"
        assert attrs["queue_name"] == "matik-enig-triggers-sandbox-queue"

    def test_record_retry_first_delivery_ignored(self) -> None:
        """receive_count <= 1 is the first delivery and records nothing."""
        meter = self._create_mock_meter()
        retry_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            retry_mock,
        ]

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_retry("my-queue", 1)

        retry_mock.add.assert_not_called()

    def test_record_processed_success(self) -> None:
        """Success path increments processed counter and records duration."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()
        messages_failed = MagicMock()
        processing_duration = MagicMock()
        meter.create_counter.side_effect = [
            messages_processed,
            messages_failed,
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.return_value = processing_duration

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_processed("my-queue", 1.5, True)

        processing_duration.record.assert_called_once()
        assert processing_duration.record.call_args[0][0] == 1.5
        assert processing_duration.record.call_args[0][1]["status"] == "success"
        messages_processed.add.assert_called_once()
        messages_failed.add.assert_not_called()

    def test_record_processed_failure(self) -> None:
        """Failure path increments failed counter with error_type label."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()
        messages_failed = MagicMock()
        processing_duration = MagicMock()
        meter.create_counter.side_effect = [
            messages_processed,
            messages_failed,
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.return_value = processing_duration

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_processed("my-queue", 2.0, False, RuntimeError("timeout"))

        assert processing_duration.record.call_args[0][1]["status"] == "failed"
        messages_processed.add.assert_not_called()
        messages_failed.add.assert_called_once()
        assert messages_failed.add.call_args[0][1]["error_type"] == "RuntimeError"

    def test_record_processed_failure_no_error(self) -> None:
        """Failure with no exception uses 'unknown' as error_type."""
        meter = self._create_mock_meter()
        messages_failed = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            messages_failed,
            MagicMock(),
            MagicMock(),
        ]

        metrics = SQSMetrics(meter, "enigmatologist")
        metrics.record_processed("my-queue", 1.0, False, None)

        assert messages_failed.add.call_args[0][1]["error_type"] == "unknown"

    def test_start_message_success_path(self) -> None:
        """start_message records success with measured duration."""
        meter = self._create_mock_meter()
        messages_processed = MagicMock()
        processing_duration = MagicMock()
        meter.create_counter.side_effect = [
            messages_processed,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.return_value = processing_duration

        metrics = SQSMetrics(meter, "enigmatologist")
        record = metrics.start_message("matik-enig-queue")
        time.sleep(0.02)
        record(True, None)

        processing_duration.record.assert_called_once()
        duration = processing_duration.record.call_args[0][0]
        assert duration >= 0.02
        messages_processed.add.assert_called_once()

    def test_start_message_failure_path(self) -> None:
        """start_message failure records exception type in failed counter."""
        meter = self._create_mock_meter()
        messages_failed = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            messages_failed,
            MagicMock(),
            MagicMock(),
        ]

        metrics = SQSMetrics(meter, "enigmatologist")
        record = metrics.start_message("matik-enig-queue")
        record(False, ValueError("bad message"))

        messages_failed.add.assert_called_once()
        assert messages_failed.add.call_args[0][1]["error_type"] == "ValueError"


class TestSQSMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Counters have proper descriptions and units."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        SQSMetrics(meter, "enigmatologist")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert "matik_sqs_poll_total" in counter_calls
        assert counter_calls["matik_sqs_poll_total"]["unit"] == "{poll}"

        assert (
            "successfully processed"
            in counter_calls["matik_sqs_processed_messages_total"]["description"]
        )
        assert (
            counter_calls["matik_sqs_processed_messages_total"]["unit"] == "{message}"
        )

        assert (
            "failed" in counter_calls["matik_sqs_failed_messages_total"]["description"]
        )
        assert counter_calls["matik_sqs_failed_messages_total"]["unit"] == "{message}"

    def test_histogram_description(self) -> None:
        """Histogram has proper description, unit, and bucket boundaries."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        SQSMetrics(meter, "enigmatologist")

        hist_call = meter.create_histogram.call_args[1]
        assert hist_call["name"] == "matik_sqs_processing_seconds"
        assert "duration" in hist_call["description"].lower()
        assert hist_call["unit"] == "s"
        assert (
            hist_call["explicit_bucket_boundaries_advisory"]
            == SQS_PROCESSING_DURATION_BUCKETS
        )
