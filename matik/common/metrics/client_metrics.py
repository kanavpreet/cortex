"""Client metrics for external API instrumentation.

Provides metrics for tracking external API requests including:
- Request counts by endpoint and status
- Request duration histograms
- Error tracking
- Retry counts
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for external client latency (in seconds)
# Includes longer buckets for external API calls which may be slower
CLIENT_LATENCY_BUCKETS = (
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


class ClientMetrics:
    """Metrics for external API client instrumentation.

    Tracks request counts, durations, errors, and retries for external
    API calls with labels for service, client, method, endpoint, and status.

    Usage:
        metrics = ClientMetrics(meter, "historian", "incidentio")

        # Record a request
        metrics.record_request(
            method="GET",
            endpoint="/api/incidents",
            duration_seconds=0.5,
            status_code=200,
        )
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
        client_name: str,
    ) -> None:
        """Initialize client metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service using this client
            client_name: Name of the external API client (e.g., "incidentio", "jira")
        """
        self._service_name = service_name
        self._client_name = client_name

        self._request_count = meter.create_counter(
            name="matik_client_requests_total",
            description="Total number of external API requests",
            unit="{request}",
        )

        self._request_duration = meter.create_histogram(
            name="matik_client_request_duration_seconds",
            description="External API request duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=CLIENT_LATENCY_BUCKETS,
        )

        self._request_errors = meter.create_counter(
            name="matik_client_request_errors_total",
            description="Total number of external API request errors",
            unit="{error}",
        )

        self._retry_count = meter.create_counter(
            name="matik_client_retries_total",
            description="Total number of external API request retries",
            unit="{retry}",
        )

    def record_request(
        self,
        method: str,
        endpoint: str,
        duration_seconds: float,
        status_code: int,
        error: Exception | None = None,
    ) -> None:
        """Record an external API request with timing and error tracking.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            duration_seconds: Request duration in seconds
            status_code: HTTP status code
            error: Exception if the request failed
        """
        status = "error" if error is not None or status_code >= 400 else "success"

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "client": self._client_name,
            "method": method,
            "endpoint": endpoint,
            "status": status,
            "status_code": status_code,
        }

        self._request_count.add(1, attrs)
        self._request_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded client request metric",
            client=self._client_name,
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            duration_seconds=round(duration_seconds, 3),
        )

        if error is not None or status_code >= 400:
            self._request_errors.add(1, attrs)
            logger.debug(
                "recorded client request error metric",
                client=self._client_name,
                method=method,
                endpoint=endpoint,
                status_code=status_code,
            )

    def record_retry(
        self,
        method: str,
        endpoint: str,
        attempt: int,
    ) -> None:
        """Record a retry attempt for an external API request.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            attempt: Retry attempt number
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "client": self._client_name,
            "method": method,
            "endpoint": endpoint,
            "attempt": attempt,
        }

        self._retry_count.add(1, attrs)
        logger.debug(
            "recorded client retry metric",
            client=self._client_name,
            method=method,
            endpoint=endpoint,
            attempt=attempt,
        )

    def start_request(
        self,
        method: str,
        endpoint: str,
    ) -> Callable[[int, Exception | None], None]:
        """Start timing a request and return a function to record completion.

        Usage:
            record = metrics.start_request("GET", "/api/incidents")
            try:
                response = await client.get("/api/incidents")
                record(response.status_code, None)
            except Exception as e:
                record(0, e)

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path

        Returns:
            A function to call when the request completes with status_code and error.
        """
        start = time.perf_counter()

        def record(status_code: int, error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_request(method, endpoint, duration, status_code, error)

        return record
