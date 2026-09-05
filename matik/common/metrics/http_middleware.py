"""HTTP metrics middleware for FastAPI.

Provides metrics for tracking HTTP requests including:
- Request counts by method, path, and status
- Request duration histograms
- Active request tracking
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import metrics
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for HTTP request latency (in seconds)
# Following Airbnb patterns with fine-grained buckets for fast requests
HTTP_LATENCY_BUCKETS = (
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


def _get_status_class(status_code: int) -> str:
    """Get the HTTP status class (2xx, 3xx, 4xx, 5xx).

    Args:
        status_code: HTTP status code

    Returns:
        Status class string
    """
    if 200 <= status_code < 300:
        return "2xx"
    elif 300 <= status_code < 400:
        return "3xx"
    elif 400 <= status_code < 500:
        return "4xx"
    elif status_code >= 500:
        return "5xx"
    else:
        return "unknown"


def _get_route_path(request: Request) -> str:
    """Extract the route pattern from the request to avoid high-cardinality.

    Uses Starlette's routing to get the path template for parameterized paths.

    Args:
        request: Starlette request object

    Returns:
        Route path template or actual path if no template available
    """
    # Try to get the route template from the router
    app = request.app
    if hasattr(app, "routes"):
        for route in app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                if hasattr(route, "path"):
                    return str(route.path)
                break

    # Fallback to request path
    return request.url.path


class HTTPMetrics:
    """HTTP metrics for request instrumentation.

    Tracks request counts, durations, and active requests with labels
    for service, method, path, and status.

    Usage with FastAPI:
        from fastapi import FastAPI
        from matik.common.metrics import HTTPMetrics, TelescopeClient

        app = FastAPI()
        telescope = TelescopeClient(config)
        telescope.start()

        http_metrics = HTTPMetrics(telescope.meter, "api")
        app.add_middleware(BaseHTTPMiddleware, dispatch=http_metrics.dispatch)

        # Or use the class method for convenience
        app.add_middleware(
            BaseHTTPMiddleware,
            dispatch=HTTPMetrics.create_dispatch(telescope.meter, "api"),
        )
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize HTTP metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service handling HTTP requests
        """
        self._service_name = service_name

        self._request_count = meter.create_counter(
            name="matik_http_requests_total",
            description="Total number of HTTP requests",
            unit="{request}",
        )

        self._request_duration = meter.create_histogram(
            name="matik_http_request_duration_seconds",
            description="HTTP request duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=HTTP_LATENCY_BUCKETS,
        )

        self._active_requests = meter.create_up_down_counter(
            name="matik_http_active_requests",
            description="Number of HTTP requests currently being processed",
            unit="{request}",
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Middleware dispatch function for recording HTTP metrics.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in the chain

        Returns:
            HTTP response
        """
        start = time.perf_counter()
        path = _get_route_path(request)
        active_attrs: dict[str, Any] = {
            "service": self._service_name,
            "method": request.method,
            "path": path,
        }

        # Track active requests
        self._active_requests.add(1, active_attrs)

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            # Record metrics
            duration = time.perf_counter() - start

            attrs: dict[str, Any] = {
                **active_attrs,
                "status": str(status_code),
                "status_class": _get_status_class(status_code),
            }

            self._request_count.add(1, attrs)
            self._request_duration.record(duration, attrs)
            self._active_requests.add(-1, active_attrs)

            logger.debug(
                "recorded http request metric",
                method=request.method,
                path=path,
                status_code=status_code,
                duration_seconds=round(duration, 3),
            )

        return response

    @classmethod
    def create_dispatch(
        cls,
        meter: metrics.Meter,
        service_name: str,
    ) -> Callable[
        [Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]
    ]:
        """Create a dispatch function for use with BaseHTTPMiddleware.

        This is a convenience method for creating the middleware dispatch function.

        Usage:
            app.add_middleware(
                BaseHTTPMiddleware,
                dispatch=HTTPMetrics.create_dispatch(meter, "api"),
            )

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service handling HTTP requests

        Returns:
            Dispatch function for BaseHTTPMiddleware
        """
        instance = cls(meter, service_name)
        return instance.dispatch

    @classmethod
    def create_middleware(
        cls,
        meter: metrics.Meter,
        service_name: str,
    ) -> type[BaseHTTPMiddleware]:
        """Create a middleware class for use with FastAPI.

        This creates a custom middleware class that can be added directly.

        Usage:
            middleware_cls = HTTPMetrics.create_middleware(meter, "api")
            app.add_middleware(middleware_cls)

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service handling HTTP requests

        Returns:
            Middleware class for FastAPI
        """
        http_metrics = cls(meter, service_name)

        class MetricsMiddleware(BaseHTTPMiddleware):
            async def dispatch(
                self,
                request: Request,
                call_next: Callable[[Request], Awaitable[Response]],
            ) -> Response:
                return await http_metrics.dispatch(request, call_next)

        return MetricsMiddleware
