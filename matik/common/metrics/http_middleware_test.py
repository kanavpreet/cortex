"""Unit tests for HTTPMetrics middleware."""

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from common.metrics.http_middleware import (
    HTTP_LATENCY_BUCKETS,
    HTTPMetrics,
    _get_status_class,
)


class TestHTTPMetricsConstants:
    """Test module constants."""

    def test_latency_buckets(self) -> None:
        """Test HTTP latency buckets are defined correctly."""
        assert HTTP_LATENCY_BUCKETS == (
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
            10.0,
        )
        # Verify buckets are in ascending order
        for i in range(len(HTTP_LATENCY_BUCKETS) - 1):
            assert HTTP_LATENCY_BUCKETS[i] < HTTP_LATENCY_BUCKETS[i + 1]


class TestGetStatusClass:
    """Test _get_status_class helper function."""

    def test_2xx_status(self) -> None:
        """Test 2xx status codes."""
        assert _get_status_class(200) == "2xx"
        assert _get_status_class(201) == "2xx"
        assert _get_status_class(204) == "2xx"
        assert _get_status_class(299) == "2xx"

    def test_3xx_status(self) -> None:
        """Test 3xx status codes."""
        assert _get_status_class(300) == "3xx"
        assert _get_status_class(301) == "3xx"
        assert _get_status_class(304) == "3xx"
        assert _get_status_class(399) == "3xx"

    def test_4xx_status(self) -> None:
        """Test 4xx status codes."""
        assert _get_status_class(400) == "4xx"
        assert _get_status_class(401) == "4xx"
        assert _get_status_class(404) == "4xx"
        assert _get_status_class(499) == "4xx"

    def test_5xx_status(self) -> None:
        """Test 5xx status codes."""
        assert _get_status_class(500) == "5xx"
        assert _get_status_class(502) == "5xx"
        assert _get_status_class(503) == "5xx"
        assert _get_status_class(599) == "5xx"

    def test_unknown_status(self) -> None:
        """Test unknown status codes."""
        assert _get_status_class(100) == "unknown"
        assert _get_status_class(199) == "unknown"
        assert _get_status_class(0) == "unknown"


class TestHTTPMetrics:
    """Test suite for HTTPMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Test that init creates all required instruments."""
        meter = self._create_mock_meter()
        HTTPMetrics(meter, "api")

        # Verify counter created
        meter.create_counter.assert_called_once()
        counter_call = meter.create_counter.call_args[1]
        assert counter_call["name"] == "matik_http_requests_total"

        # Verify histogram created
        meter.create_histogram.assert_called_once()
        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_http_request_duration_seconds"

        # Verify up_down_counter created
        meter.create_up_down_counter.assert_called_once()
        updown_call = meter.create_up_down_counter.call_args[1]
        assert updown_call["name"] == "matik_http_active_requests"

    @pytest.mark.anyio
    async def test_dispatch_records_metrics(self) -> None:
        """Test that dispatch records metrics correctly."""
        meter = self._create_mock_meter()
        request_count = MagicMock()
        request_duration = MagicMock()
        active_requests = MagicMock()

        meter.create_counter.return_value = request_count
        meter.create_histogram.return_value = request_duration
        meter.create_up_down_counter.return_value = active_requests

        http_metrics = HTTPMetrics(meter, "api")

        # Create mock app
        mock_app = MagicMock()
        mock_app.routes = []

        # Create mock request with app in scope
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/incidents",
            "query_string": b"",
            "headers": [],
            "server": ("localhost", 8000),
            "app": mock_app,
        }
        request = Request(scope)

        # Create mock response
        async def call_next(request: Request) -> Response:
            return Response(status_code=200)

        # Call dispatch
        response = await http_metrics.dispatch(request, call_next)

        # Verify active requests incremented and decremented with path label
        assert active_requests.add.call_count == 2
        active_attrs = {"service": "api", "method": "GET", "path": "/api/v1/incidents"}
        active_requests.add.assert_any_call(1, active_attrs)
        active_requests.add.assert_any_call(-1, active_attrs)

        # Verify request count recorded
        request_count.add.assert_called_once()
        attrs = request_count.add.call_args[0][1]
        assert attrs["service"] == "api"
        assert attrs["method"] == "GET"
        assert attrs["status"] == "200"
        assert attrs["status_class"] == "2xx"

        # Verify duration recorded
        request_duration.record.assert_called_once()

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_dispatch_handles_exception(self) -> None:
        """Test that dispatch handles exceptions and still records metrics."""
        meter = self._create_mock_meter()
        request_count = MagicMock()
        active_requests = MagicMock()

        meter.create_counter.return_value = request_count
        meter.create_up_down_counter.return_value = active_requests

        http_metrics = HTTPMetrics(meter, "api")

        # Create mock app
        mock_app = MagicMock()
        mock_app.routes = []

        # Create mock request with app in scope
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/incidents",
            "query_string": b"",
            "headers": [],
            "server": ("localhost", 8000),
            "app": mock_app,
        }
        request = Request(scope)

        # Create mock that raises
        async def call_next(request: Request) -> Response:
            raise ValueError("Internal error")

        # Call dispatch - should raise
        with pytest.raises(ValueError):
            await http_metrics.dispatch(request, call_next)

        # Verify metrics still recorded with 500 status
        attrs = request_count.add.call_args[0][1]
        assert attrs["status"] == "500"
        assert attrs["status_class"] == "5xx"

        # Verify active requests decremented with path label
        active_requests.add.assert_any_call(
            -1, {"service": "api", "method": "POST", "path": "/api/v1/incidents"}
        )

    def test_create_dispatch(self) -> None:
        """Test create_dispatch class method."""
        meter = self._create_mock_meter()

        dispatch_func = HTTPMetrics.create_dispatch(meter, "api")

        # Verify it returns a callable
        assert callable(dispatch_func)

    def test_create_middleware(self) -> None:
        """Test create_middleware class method."""
        meter = self._create_mock_meter()

        middleware_cls = HTTPMetrics.create_middleware(meter, "api")

        # Verify it returns a class
        assert isinstance(middleware_cls, type)


class TestHTTPMetricsIntegration:
    """Integration tests for HTTPMetrics with FastAPI."""

    def test_with_fastapi_app(self) -> None:
        """Test HTTPMetrics works with FastAPI application."""
        from fastapi import FastAPI

        meter = MagicMock()
        request_count = MagicMock()
        request_duration = MagicMock()
        active_requests = MagicMock()

        meter.create_counter.return_value = request_count
        meter.create_histogram.return_value = request_duration
        meter.create_up_down_counter.return_value = active_requests

        app = FastAPI()

        # Add middleware using create_middleware
        middleware_cls = HTTPMetrics.create_middleware(meter, "api")
        app.add_middleware(middleware_cls)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        # Make a request
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200

        # Verify metrics were recorded
        request_count.add.assert_called()
        request_duration.record.assert_called()


class TestHTTPMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_counter_description(self) -> None:
        """Test that counter has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()

        HTTPMetrics(meter, "api")

        counter_call = meter.create_counter.call_args[1]
        assert counter_call["name"] == "matik_http_requests_total"
        assert "Total number of HTTP requests" in counter_call["description"]
        assert counter_call["unit"] == "{request}"

    def test_histogram_description(self) -> None:
        """Test that histogram has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()

        HTTPMetrics(meter, "api")

        histogram_call = meter.create_histogram.call_args[1]
        assert histogram_call["name"] == "matik_http_request_duration_seconds"
        assert "duration" in histogram_call["description"].lower()
        assert histogram_call["unit"] == "s"
        assert (
            histogram_call["explicit_bucket_boundaries_advisory"]
            == HTTP_LATENCY_BUCKETS
        )

    def test_up_down_counter_description(self) -> None:
        """Test that up_down_counter has proper description."""
        meter = MagicMock()
        meter.create_counter.return_value = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_up_down_counter.return_value = MagicMock()

        HTTPMetrics(meter, "api")

        updown_call = meter.create_up_down_counter.call_args[1]
        assert updown_call["name"] == "matik_http_active_requests"
        assert "being processed" in updown_call["description"].lower()
        assert updown_call["unit"] == "{request}"
