"""Unit tests for EnricherMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.enricher_metrics import (
    ENRICHER_PROCESSING_DURATION_BUCKETS,
    EnricherMetrics,
)


def _make_meter() -> MagicMock:
    """Create a mock OpenTelemetry meter."""
    meter = MagicMock()
    meter.create_counter.return_value = MagicMock()
    meter.create_histogram.return_value = MagicMock()
    meter.create_gauge.return_value = MagicMock()
    return meter


class TestEnricherMetricsConstants:
    """Tests for module-level constants."""

    def test_processing_duration_buckets_are_ordered(self) -> None:
        """Histogram buckets are in ascending order."""
        buckets = ENRICHER_PROCESSING_DURATION_BUCKETS
        for i in range(len(buckets) - 1):
            assert buckets[i] < buckets[i + 1]

    def test_processing_duration_buckets_values(self) -> None:
        """Histogram buckets contain expected values."""
        assert 1.0 in ENRICHER_PROCESSING_DURATION_BUCKETS
        assert 60.0 in ENRICHER_PROCESSING_DURATION_BUCKETS
        assert 300.0 in ENRICHER_PROCESSING_DURATION_BUCKETS


class TestEnricherMetricsInit:
    """Tests for EnricherMetrics instrument creation."""

    def test_creates_instruments(self) -> None:
        """Init creates exactly 6 counters, 1 histogram, and 1 gauge."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        assert meter.create_counter.call_count == 6
        assert meter.create_histogram.call_count == 1
        assert meter.create_gauge.call_count == 1

    def test_counter_names(self) -> None:
        """All four counters have the correct metric names."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        counter_names = {c[1]["name"] for c in meter.create_counter.call_args_list}
        assert "matik_enricher_messages_processed_total" in counter_names
        assert "matik_enricher_enrichment_operations_total" in counter_names
        assert "matik_enricher_dlq_messages_total" in counter_names
        assert "matik_enricher_backoff_total" in counter_names
        assert "matik_enricher_hash_cache_operations_total" in counter_names
        assert "matik_enricher_enrichment_cache_operations_total" in counter_names

    def test_histogram_name(self) -> None:
        """The histogram has the correct metric name."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        hist_call = meter.create_histogram.call_args[1]
        assert hist_call["name"] == "matik_enricher_message_processing_duration_seconds"

    def test_gauge_name(self) -> None:
        """The gauge has the correct metric name."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        gauge_call = meter.create_gauge.call_args[1]
        assert gauge_call["name"] == "matik_enricher_queue_depth"

    def test_counter_units(self) -> None:
        """Counters use the {message} or {event} unit."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }
        assert (
            counter_calls["matik_enricher_messages_processed_total"]["unit"]
            == "{message}"
        )
        assert counter_calls["matik_enricher_dlq_messages_total"]["unit"] == "{message}"
        assert counter_calls["matik_enricher_backoff_total"]["unit"] == "{event}"

    def test_histogram_bucket_boundaries(self) -> None:
        """Histogram uses the module-level bucket boundaries constant."""
        meter = _make_meter()
        EnricherMetrics(meter, "enricher")

        hist_call = meter.create_histogram.call_args[1]
        assert (
            hist_call["explicit_bucket_boundaries_advisory"]
            == ENRICHER_PROCESSING_DURATION_BUCKETS
        )


class TestEnricherMetricsRecordProcessed:
    """Tests for record_processed method."""

    def _make_metrics(self) -> tuple[EnricherMetrics, MagicMock, MagicMock]:
        """Return (EnricherMetrics, messages_processed counter mock, histogram mock)."""
        meter = _make_meter()
        processed_mock = MagicMock()
        histogram_mock = MagicMock()
        meter.create_counter.side_effect = [
            processed_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.return_value = histogram_mock
        return EnricherMetrics(meter, "enricher"), processed_mock, histogram_mock

    def test_record_processed_success_increments_counter(self) -> None:
        """record_processed success path increments the processed counter."""
        metrics, processed_mock, _histogram_mock = self._make_metrics()
        metrics.record_processed("incidentio", 5.0, True)

        processed_mock.add.assert_called_once()
        attrs = processed_mock.add.call_args[0][1]
        assert attrs["source_type"] == "incidentio"
        assert "enrichment_type" not in attrs
        assert attrs["status"] == "success"

    def test_record_processed_failure_uses_failed_status(self) -> None:
        """record_processed failure path uses status='failed'."""
        metrics, processed_mock, _ = self._make_metrics()
        metrics.record_processed("incidentio", 3.0, False)

        attrs = processed_mock.add.call_args[0][1]
        assert attrs["status"] == "failed"

    def test_record_processed_records_duration(self) -> None:
        """record_processed records duration to the histogram."""
        metrics, _, histogram_mock = self._make_metrics()
        metrics.record_processed("incidentio", 7.5, True)

        histogram_mock.record.assert_called_once()
        assert histogram_mock.record.call_args[0][0] == 7.5


class TestEnricherMetricsRecordDLQ:
    """Tests for record_dlq method."""

    def test_record_dlq_with_exception(self) -> None:
        """record_dlq records error_type from the provided exception."""
        meter = _make_meter()
        dlq_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            dlq_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_dlq("incidentio", ValueError("bad content"))

        dlq_mock.add.assert_called_once()
        attrs = dlq_mock.add.call_args[0][1]
        assert attrs["source_type"] == "incidentio"
        assert attrs["error_type"] == "ValueError"

    def test_record_dlq_without_exception(self) -> None:
        """record_dlq uses 'unknown' error_type when no exception is provided."""
        meter = _make_meter()
        dlq_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            dlq_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_dlq("incidentio", None)

        attrs = dlq_mock.add.call_args[0][1]
        assert attrs["error_type"] == "unknown"


class TestEnricherMetricsRecordBackoff:
    """Tests for record_backoff method."""

    def test_record_backoff_passes_correct_attrs(self) -> None:
        """record_backoff passes source_type and retry_attempt as string label."""
        meter = _make_meter()
        backoff_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            backoff_mock,
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_backoff("ghe_pr", 2)

        backoff_mock.add.assert_called_once()
        attrs = backoff_mock.add.call_args[0][1]
        assert attrs["source_type"] == "ghe_pr"
        assert attrs["retry_attempt"] == "2"


class TestEnricherMetricsRecordQueueDepth:
    """Tests for record_queue_depth method."""

    def test_record_queue_depth_enricher(self) -> None:
        """record_queue_depth records depth for the enricher queue label."""
        meter = _make_meter()
        gauge_mock = MagicMock()
        meter.create_gauge.return_value = gauge_mock
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_queue_depth("enricher", 150)

        gauge_mock.set.assert_called_once()
        value, attrs = gauge_mock.set.call_args[0]
        assert value == 150
        assert attrs["queue"] == "enricher"

    def test_record_queue_depth_dlq(self) -> None:
        """record_queue_depth records depth for the dlq label."""
        meter = _make_meter()
        gauge_mock = MagicMock()
        meter.create_gauge.return_value = gauge_mock
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_queue_depth("dlq", 3)

        value, attrs = gauge_mock.set.call_args[0]
        assert value == 3
        assert attrs["queue"] == "dlq"


class TestEnricherMetricsStartMessage:
    """Tests for the start_message timing helper."""

    def test_start_message_records_success_with_duration(self) -> None:
        """start_message records a successful processing event with measured duration."""
        meter = _make_meter()
        processed_mock = MagicMock()
        histogram_mock = MagicMock()
        meter.create_counter.side_effect = [
            processed_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        meter.create_histogram.return_value = histogram_mock
        metrics = EnricherMetrics(meter, "enricher")

        record = metrics.start_message("incidentio")
        time.sleep(0.01)
        record(True, None)

        histogram_mock.record.assert_called_once()
        duration = histogram_mock.record.call_args[0][0]
        assert duration >= 0.01

    def test_start_message_failure_records_failure_status(self) -> None:
        """start_message failure path records status=failed."""
        meter = _make_meter()
        processed_mock = MagicMock()
        meter.create_counter.side_effect = [
            processed_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        record = metrics.start_message("jira")
        record(False, RuntimeError("timeout"))

        attrs = processed_mock.add.call_args[0][1]
        assert attrs["status"] == "failed"
        assert attrs["source_type"] == "jira"
        assert "enrichment_type" not in attrs


class TestEnricherMetricsRecordEnrichmentOperation:
    """Tests for record_enrichment_operation per-Facade-call metric."""

    def test_record_enrichment_operation_success(self) -> None:
        """record_enrichment_operation records the operation name as enrichment_type."""
        meter = _make_meter()
        ops_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            ops_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_enrichment_operation("incidentio", "root_cause_summary", True)

        ops_mock.add.assert_called_once()
        attrs = ops_mock.add.call_args[0][1]
        assert attrs["source_type"] == "incidentio"
        assert attrs["enrichment_type"] == "root_cause_summary"
        assert attrs["status"] == "success"

    def test_record_enrichment_operation_failure(self) -> None:
        """record_enrichment_operation records status=failed on failure."""
        meter = _make_meter()
        ops_mock = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            ops_mock,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        metrics = EnricherMetrics(meter, "enricher")

        metrics.record_enrichment_operation(
            "incidentio", "resolution_summary", False, RuntimeError("timeout")
        )

        attrs = ops_mock.add.call_args[0][1]
        assert attrs["enrichment_type"] == "resolution_summary"
        assert attrs["status"] == "failed"


class TestEnricherMetricsRecordEnrichmentCache:
    """Tests for record_enrichment_cache hash-dedup metric."""

    def _make_metrics(self) -> tuple[EnricherMetrics, MagicMock]:
        """Return (EnricherMetrics, enrichment_cache_ops counter mock)."""
        meter = _make_meter()
        cache_mock = MagicMock()
        # enrichment_cache_ops is the 6th (last) counter created.
        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            cache_mock,
        ]
        return EnricherMetrics(meter, "enricher"), cache_mock

    def test_record_hit(self) -> None:
        """A hit records result='hit' with source_type and enrichment_type."""
        metrics, cache_mock = self._make_metrics()
        metrics.record_enrichment_cache("ghe_pr", "pull_request_summary", "hit")

        cache_mock.add.assert_called_once()
        count, attrs = cache_mock.add.call_args[0]
        assert count == 1
        assert attrs["service"] == "enricher"
        assert attrs["source_type"] == "ghe_pr"
        assert attrs["enrichment_type"] == "pull_request_summary"
        assert attrs["result"] == "hit"

    def test_record_miss_and_new(self) -> None:
        """miss and new results are passed through verbatim."""
        metrics, cache_mock = self._make_metrics()
        metrics.record_enrichment_cache("jira", "issue_summary", "miss")
        metrics.record_enrichment_cache("jira", "issue_comments_summary", "new")

        results = {c[0][1]["result"] for c in cache_mock.add.call_args_list}
        assert results == {"miss", "new"}

    def test_record_with_count(self) -> None:
        """The count argument is forwarded to the counter."""
        metrics, cache_mock = self._make_metrics()
        metrics.record_enrichment_cache("incidentio", "root_cause_summary", "hit", 4)

        count, _attrs = cache_mock.add.call_args[0]
        assert count == 4
