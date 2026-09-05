"""Unit tests for IncidentIOAPIMetrics."""

from unittest.mock import MagicMock

from common.metrics.incidentio_api_metrics import (
    BATCH_SIZE_BUCKETS,
    IncidentIOAPIMetrics,
)


class TestIncidentIOAPIMetricsConstants:
    """Test module constants."""

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


class TestIncidentIOAPIMetrics:
    """Test suite for IncidentIOAPIMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        IncidentIOAPIMetrics(meter, "api")

        # Verify histogram created
        histogram_calls = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_incidentio_api_batch_size" in histogram_calls

        # Verify counter created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_incidentio_api_entities_affected_total" in counter_calls

    def test_record_batch_incident(self) -> None:
        """Test recording a batch operation for incidents."""
        meter = self._create_mock_meter()
        batch_size_histogram = MagicMock()
        entities_counter = MagicMock()

        meter.create_histogram.return_value = batch_size_histogram
        meter.create_counter.return_value = entities_counter

        metrics = IncidentIOAPIMetrics(meter, "api")
        metrics.record_batch(
            entity_type="incident",
            batch_size=100,
            affected_rows=98,
        )

        # Verify batch size recorded
        batch_size_histogram.record.assert_called_once()
        call_args = batch_size_histogram.record.call_args
        assert call_args[0][0] == 100
        attrs = call_args[0][1]
        assert attrs["service"] == "api"
        assert attrs["entity_type"] == "incident"

        # Verify entities affected recorded
        entities_counter.add.assert_called_once()
        call_args = entities_counter.add.call_args
        assert call_args[0][0] == 98
        attrs = call_args[0][1]
        assert attrs["service"] == "api"
        assert attrs["entity_type"] == "incident"

    def test_record_batch_tracker(self) -> None:
        """Test recording a batch operation for trackers."""
        meter = self._create_mock_meter()
        batch_size_histogram = MagicMock()
        entities_counter = MagicMock()

        meter.create_histogram.return_value = batch_size_histogram
        meter.create_counter.return_value = entities_counter

        metrics = IncidentIOAPIMetrics(meter, "api")
        metrics.record_batch(
            entity_type="tracker",
            batch_size=50,
            affected_rows=50,
        )

        # Verify correct entity type
        call_args = batch_size_histogram.record.call_args
        assert call_args[0][1]["entity_type"] == "tracker"


class TestIncidentIOAPIMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_histogram_description(self) -> None:
        """Test that histogram has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        IncidentIOAPIMetrics(meter, "api")

        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_incidentio_api_batch_size"
        assert "batch size" in histogram_call["description"].lower()
        assert histogram_call["unit"] == "{item}"
        assert (
            histogram_call["explicit_bucket_boundaries_advisory"] == BATCH_SIZE_BUCKETS
        )

    def test_counter_description(self) -> None:
        """Test that counter has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        IncidentIOAPIMetrics(meter, "api")

        counter_call = meter.create_counter.call_args[1]
        assert counter_call["name"] == "matik_incidentio_api_entities_affected_total"
        assert "entities affected" in counter_call["description"].lower()
        assert counter_call["unit"] == "{entity}"
