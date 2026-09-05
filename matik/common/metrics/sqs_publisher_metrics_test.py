"""Unit tests for SQSPublisherMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.sqs_publisher_metrics import (
    SQS_PUBLISH_DURATION_BUCKETS,
    SQSPublisherMetrics,
)


class TestSQSPublisherMetricsConstants:
    """Test module constants."""

    def test_publish_duration_buckets_are_sorted(self) -> None:
        """Publish duration buckets are defined and strictly ascending."""
        assert len(SQS_PUBLISH_DURATION_BUCKETS) > 0
        for i in range(len(SQS_PUBLISH_DURATION_BUCKETS) - 1):
            assert SQS_PUBLISH_DURATION_BUCKETS[i] < SQS_PUBLISH_DURATION_BUCKETS[i + 1]


class TestSQSPublisherMetrics:
    """Test suite for SQSPublisherMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Init creates all required instruments."""
        meter = self._create_mock_meter()
        SQSPublisherMetrics(meter, "historian-jira")

        counter_names = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_sqs_messages_published_total" in counter_names
        assert "matik_sqs_messages_publish_failed_total" in counter_names

        histogram_names = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_sqs_publish_duration_seconds" in histogram_names

    def test_record_publish_success(self) -> None:
        """Success path increments published counter and records duration."""
        meter = self._create_mock_meter()
        messages_published = MagicMock()
        messages_failed = MagicMock()
        publish_duration = MagicMock()
        meter.create_counter.side_effect = [messages_published, messages_failed]
        meter.create_histogram.return_value = publish_duration

        metrics = SQSPublisherMetrics(meter, "historian-jira")
        metrics.record_publish("matik-scribe-queue", "JiraBaseMessage", 0.15, True)

        publish_duration.record.assert_called_once()
        duration, attrs = publish_duration.record.call_args[0]
        assert duration == 0.15
        assert attrs["status"] == "success"
        assert attrs["service"] == "historian-jira"
        assert attrs["queue_name"] == "matik-scribe-queue"
        assert attrs["message_type"] == "JiraBaseMessage"
        messages_published.add.assert_called_once()
        messages_failed.add.assert_not_called()

    def test_record_publish_failure(self) -> None:
        """Failure path increments failed counter with error_type label."""
        meter = self._create_mock_meter()
        messages_published = MagicMock()
        messages_failed = MagicMock()
        publish_duration = MagicMock()
        meter.create_counter.side_effect = [messages_published, messages_failed]
        meter.create_histogram.return_value = publish_duration

        metrics = SQSPublisherMetrics(meter, "historian-jira")
        metrics.record_publish(
            "matik-scribe-queue", "JiraBaseMessage", 0.5, False, RuntimeError("timeout")
        )

        assert publish_duration.record.call_args[0][1]["status"] == "failed"
        messages_published.add.assert_not_called()
        messages_failed.add.assert_called_once()
        assert messages_failed.add.call_args[0][1]["error_type"] == "RuntimeError"

    def test_record_publish_failure_no_error(self) -> None:
        """Failure with no exception uses 'unknown' as error_type."""
        meter = self._create_mock_meter()
        messages_failed = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), messages_failed]

        metrics = SQSPublisherMetrics(meter, "historian-jira")
        metrics.record_publish("matik-scribe-queue", "JiraBaseMessage", 1.0, False)

        assert messages_failed.add.call_args[0][1]["error_type"] == "unknown"

    def test_start_publish_success_path(self) -> None:
        """start_publish records success with measured duration."""
        meter = self._create_mock_meter()
        messages_published = MagicMock()
        publish_duration = MagicMock()
        meter.create_counter.side_effect = [messages_published, MagicMock()]
        meter.create_histogram.return_value = publish_duration

        metrics = SQSPublisherMetrics(meter, "historian-jira")
        record = metrics.start_publish("matik-scribe-queue", "JiraBaseMessage")
        time.sleep(0.02)
        record(True, None)

        publish_duration.record.assert_called_once()
        duration = publish_duration.record.call_args[0][0]
        assert duration >= 0.02
        messages_published.add.assert_called_once()

    def test_start_publish_failure_path(self) -> None:
        """start_publish failure records exception type in failed counter."""
        meter = self._create_mock_meter()
        messages_failed = MagicMock()
        meter.create_counter.side_effect = [MagicMock(), messages_failed]

        metrics = SQSPublisherMetrics(meter, "historian-jira")
        record = metrics.start_publish("matik-scribe-queue", "JiraBaseMessage")
        record(False, ValueError("serialization error"))

        messages_failed.add.assert_called_once()
        assert messages_failed.add.call_args[0][1]["error_type"] == "ValueError"


class TestSQSPublisherMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Counters have proper descriptions and units."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        SQSPublisherMetrics(meter, "historian-jira")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert (
            "successfully published"
            in counter_calls["matik_sqs_messages_published_total"]["description"]
        )
        assert (
            counter_calls["matik_sqs_messages_published_total"]["unit"] == "{message}"
        )

        assert (
            "failed to publish"
            in counter_calls["matik_sqs_messages_publish_failed_total"]["description"]
        )
        assert (
            counter_calls["matik_sqs_messages_publish_failed_total"]["unit"]
            == "{message}"
        )

    def test_histogram_description(self) -> None:
        """Histogram has proper description, unit, and bucket boundaries."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        SQSPublisherMetrics(meter, "historian-jira")

        hist_call = meter.create_histogram.call_args[1]
        assert hist_call["name"] == "matik_sqs_publish_duration_seconds"
        assert "duration" in hist_call["description"].lower()
        assert hist_call["unit"] == "s"
        assert (
            hist_call["explicit_bucket_boundaries_advisory"]
            == SQS_PUBLISH_DURATION_BUCKETS
        )
