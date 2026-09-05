"""Unit tests for ChroniclerMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.chronicler_metrics import (
    CHRONICLER_POLL_DURATION_BUCKETS,
    CHRONICLER_PROCESSING_DURATION_BUCKETS,
    ChroniclerMetrics,
)


def _make_meter() -> MagicMock:
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    meter.create_histogram.return_value = MagicMock()
    return meter


class TestChroniclerMetricsConstants:
    def test_processing_buckets_ordered(self) -> None:
        buckets = CHRONICLER_PROCESSING_DURATION_BUCKETS
        for i in range(len(buckets) - 1):
            assert buckets[i] < buckets[i + 1]

    def test_poll_buckets_ordered(self) -> None:
        buckets = CHRONICLER_POLL_DURATION_BUCKETS
        for i in range(len(buckets) - 1):
            assert buckets[i] < buckets[i + 1]

    def test_poll_buckets_smaller_than_processing(self) -> None:
        # Poll buckets target sub-second behavior; processing covers up to ~minute.
        assert max(CHRONICLER_POLL_DURATION_BUCKETS) < max(
            CHRONICLER_PROCESSING_DURATION_BUCKETS
        )


class TestChroniclerMetricsInit:
    def test_creates_expected_instruments(self) -> None:
        meter = _make_meter()
        ChroniclerMetrics(meter)
        # 5 counters: received, processed, signature_validations, publish_outcome, kafka_errors.
        # 2 histograms: processing_duration, poll_duration.
        assert meter.create_counter.call_count == 5
        assert meter.create_histogram.call_count == 2

    def test_counter_names(self) -> None:
        meter = _make_meter()
        ChroniclerMetrics(meter)
        names = {c[1]["name"] for c in meter.create_counter.call_args_list}
        assert names == {
            "matik_chronicler_messages_received_total",
            "matik_chronicler_messages_processed_total",
            "matik_chronicler_signature_validations_total",
            "matik_chronicler_publish_outcome_total",
            "matik_chronicler_kafka_errors_total",
        }

    def test_histogram_names(self) -> None:
        meter = _make_meter()
        ChroniclerMetrics(meter)
        names = {h[1]["name"] for h in meter.create_histogram.call_args_list}
        assert names == {
            "matik_chronicler_message_processing_duration_seconds",
            "matik_chronicler_kafka_poll_duration_seconds",
        }

    def test_processing_histogram_uses_processing_buckets(self) -> None:
        meter = _make_meter()
        ChroniclerMetrics(meter)
        by_name = {h[1]["name"]: h[1] for h in meter.create_histogram.call_args_list}
        assert (
            by_name["matik_chronicler_message_processing_duration_seconds"][
                "explicit_bucket_boundaries_advisory"
            ]
            == CHRONICLER_PROCESSING_DURATION_BUCKETS
        )
        assert (
            by_name["matik_chronicler_kafka_poll_duration_seconds"][
                "explicit_bucket_boundaries_advisory"
            ]
            == CHRONICLER_POLL_DURATION_BUCKETS
        )


class TestChroniclerMetricsEmission:
    def _setup(self) -> ChroniclerMetrics:
        return ChroniclerMetrics(_make_meter(), "chronicler")

    def test_record_received_emits_counter(self) -> None:
        m = self._setup()
        m.record_received("yoyo.callback.matik_sandbox_ghe_webhook_events")
        m._messages_received.add.assert_called_once_with(  # type: ignore[attr-defined]
            1,
            {
                "service": "chronicler",
                "topic": "yoyo.callback.matik_sandbox_ghe_webhook_events",
            },
        )

    def test_record_processed_emits_counter_and_histogram(self) -> None:
        m = self._setup()
        m.record_processed("topic-1", "github", "success", 0.42)
        attrs = {
            "service": "chronicler",
            "topic": "topic-1",
            "provider": "github",
            "status": "success",
        }
        m._messages_processed.add.assert_called_once_with(1, attrs)  # type: ignore[attr-defined]
        m._processing_duration.record.assert_called_once_with(0.42, attrs)  # type: ignore[attr-defined]

    def test_record_signature_results(self) -> None:
        m = self._setup()
        m.record_signature("github", "valid")
        m.record_signature("jira", "skipped_no_scheme")
        m.record_signature("incidentio", "invalid")
        m.record_signature("github", "skipped_no_secret")
        assert m._signature_validations.add.call_count == 4  # type: ignore[attr-defined]

    def test_record_publish_success_and_failure(self) -> None:
        m = self._setup()
        m.record_publish("scribe", "github", True)
        m.record_publish("enricher", "github", False)
        calls = m._publish_outcome.add.call_args_list  # type: ignore[attr-defined]
        assert calls[0][0][1]["target"] == "scribe"
        assert calls[0][0][1]["status"] == "success"
        assert calls[1][0][1]["target"] == "enricher"
        assert calls[1][0][1]["status"] == "failed"

    def test_record_kafka_poll(self) -> None:
        m = self._setup()
        m.record_kafka_poll(0.05)
        m._poll_duration.record.assert_called_once_with(  # type: ignore[attr-defined]
            0.05, {"service": "chronicler"}
        )

    def test_record_kafka_error(self) -> None:
        m = self._setup()
        m.record_kafka_error("KafkaError._PARTITION_EOF")
        m._kafka_errors.add.assert_called_once_with(  # type: ignore[attr-defined]
            1,
            {"service": "chronicler", "error_type": "KafkaError._PARTITION_EOF"},
        )


class TestStartMessage:
    def test_returns_callable(self) -> None:
        meter = _make_meter()
        m = ChroniclerMetrics(meter)
        record = m.start_message("topic-1")
        assert callable(record)

    def test_recorder_finalizes_with_provider_status_and_duration(self) -> None:
        meter = _make_meter()
        m = ChroniclerMetrics(meter)
        record = m.start_message("topic-1")
        # Sleep a tiny bit so we can assert a positive duration.
        time.sleep(0.005)
        record("github", "success")
        m._messages_processed.add.assert_called_once()  # type: ignore[attr-defined]
        m._processing_duration.record.assert_called_once()  # type: ignore[attr-defined]
        # First positional arg of the histogram .record call is the duration in seconds.
        recorded_duration = m._processing_duration.record.call_args[0][0]  # type: ignore[attr-defined]
        assert recorded_duration > 0
        # Attribute set matches what record_processed expects.
        attrs = m._processing_duration.record.call_args[0][1]  # type: ignore[attr-defined]
        assert attrs["topic"] == "topic-1"
        assert attrs["provider"] == "github"
        assert attrs["status"] == "success"
