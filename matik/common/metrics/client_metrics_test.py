"""Unit tests for ClientMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.client_metrics import CLIENT_LATENCY_BUCKETS, ClientMetrics


class TestClientMetricsConstants:
    """Test module constants."""

    def test_latency_buckets(self) -> None:
        """Test client latency buckets are defined correctly."""
        assert CLIENT_LATENCY_BUCKETS == (
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
            30.0,
            60.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(CLIENT_LATENCY_BUCKETS) - 1):
            assert CLIENT_LATENCY_BUCKETS[i] < CLIENT_LATENCY_BUCKETS[i + 1]


class TestClientMetrics:
    """Test suite for ClientMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        ClientMetrics(meter, "historian", "incidentio")

        # Verify counters created
        counter_calls = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_client_requests_total" in counter_calls
        assert "matik_client_request_errors_total" in counter_calls
        assert "matik_client_retries_total" in counter_calls

        # Verify histogram created
        histogram_calls = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_client_request_duration_seconds" in histogram_calls

    def test_record_request_success(self) -> None:
        """Test recording a successful request."""
        meter = self._create_mock_meter()
        request_count = MagicMock()
        request_duration = MagicMock()
        request_errors = MagicMock()

        meter.create_counter.side_effect = [
            request_count,
            request_errors,
            MagicMock(),
        ]
        meter.create_histogram.return_value = request_duration

        metrics = ClientMetrics(meter, "historian", "incidentio")
        metrics.record_request(
            method="GET",
            endpoint="/api/incidents",
            duration_seconds=0.5,
            status_code=200,
        )

        # Verify request count incremented
        request_count.add.assert_called_once()
        call_args = request_count.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["client"] == "incidentio"
        assert attrs["method"] == "GET"
        assert attrs["endpoint"] == "/api/incidents"
        assert attrs["status"] == "success"
        assert attrs["status_code"] == 200

        # Verify duration recorded
        request_duration.record.assert_called_once()
        assert request_duration.record.call_args[0][0] == 0.5

        # Verify errors not incremented
        request_errors.add.assert_not_called()

    def test_record_request_error_by_status_code(self) -> None:
        """Test recording a request with error status code."""
        meter = self._create_mock_meter()
        request_count = MagicMock()
        request_duration = MagicMock()
        request_errors = MagicMock()

        meter.create_counter.side_effect = [
            request_count,
            request_errors,
            MagicMock(),
        ]
        meter.create_histogram.return_value = request_duration

        metrics = ClientMetrics(meter, "historian", "incidentio")
        metrics.record_request(
            method="POST",
            endpoint="/api/incidents",
            duration_seconds=1.0,
            status_code=500,
        )

        # Verify error count incremented
        request_errors.add.assert_called_once()
        attrs = request_count.add.call_args[0][1]
        assert attrs["status"] == "error"

    def test_record_request_error_by_exception(self) -> None:
        """Test recording a request with an exception."""
        meter = self._create_mock_meter()
        request_errors = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            request_errors,
            MagicMock(),
        ]

        metrics = ClientMetrics(meter, "historian", "jira")
        metrics.record_request(
            method="GET",
            endpoint="/rest/api/2/issue",
            duration_seconds=0.1,
            status_code=0,
            error=TimeoutError("Connection timeout"),
        )

        # Verify error count incremented
        request_errors.add.assert_called_once()

    def test_record_retry(self) -> None:
        """Test recording a retry attempt."""
        meter = self._create_mock_meter()
        retry_count = MagicMock()

        meter.create_counter.side_effect = [
            MagicMock(),
            MagicMock(),
            retry_count,
        ]

        metrics = ClientMetrics(meter, "historian", "incidentio")
        metrics.record_retry(
            method="GET",
            endpoint="/api/incidents",
            attempt=2,
        )

        retry_count.add.assert_called_once()
        call_args = retry_count.add.call_args
        assert call_args[0][0] == 1
        attrs = call_args[0][1]
        assert attrs["service"] == "historian"
        assert attrs["client"] == "incidentio"
        assert attrs["method"] == "GET"
        assert attrs["endpoint"] == "/api/incidents"
        assert attrs["attempt"] == 2

    def test_start_request_timing(self) -> None:
        """Test that start_request measures duration correctly."""
        meter = self._create_mock_meter()
        request_duration = MagicMock()
        meter.create_histogram.return_value = request_duration

        metrics = ClientMetrics(meter, "historian", "incidentio")

        # Start request
        record = metrics.start_request("GET", "/api/incidents")

        # Simulate some work
        time.sleep(0.05)

        # Record completion
        record(200, None)

        # Verify duration is recorded and is at least 50ms
        request_duration.record.assert_called_once()
        recorded_duration = request_duration.record.call_args[0][0]
        assert recorded_duration >= 0.05

    def test_start_request_with_error(self) -> None:
        """Test start_request records errors correctly."""
        meter = self._create_mock_meter()
        request_errors = MagicMock()
        meter.create_counter.side_effect = [
            MagicMock(),
            request_errors,
            MagicMock(),
        ]

        metrics = ClientMetrics(meter, "api", "greenroom")
        record = metrics.start_request("GET", "/api/v1/entities")
        record(0, ConnectionError("Failed to connect"))

        request_errors.add.assert_called_once()


class TestClientMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_descriptions(self) -> None:
        """Test that counters have proper descriptions."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        ClientMetrics(meter, "historian", "incidentio")

        counter_calls = {
            c[1]["name"]: c[1] for c in meter.create_counter.call_args_list
        }

        assert (
            "Total number of external API requests"
            in counter_calls["matik_client_requests_total"]["description"]
        )
        assert counter_calls["matik_client_requests_total"]["unit"] == "{request}"

        assert (
            "Total number of external API request errors"
            in counter_calls["matik_client_request_errors_total"]["description"]
        )
        assert counter_calls["matik_client_request_errors_total"]["unit"] == "{error}"

        assert (
            "Total number of external API request retries"
            in counter_calls["matik_client_retries_total"]["description"]
        )
        assert counter_calls["matik_client_retries_total"]["unit"] == "{retry}"

    def test_histogram_description(self) -> None:
        """Test that histogram has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()

        ClientMetrics(meter, "historian", "incidentio")

        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_client_request_duration_seconds"
        assert "request duration" in histogram_call["description"].lower()
        assert histogram_call["unit"] == "s"
        assert (
            histogram_call["explicit_bucket_boundaries_advisory"]
            == CLIENT_LATENCY_BUCKETS
        )
