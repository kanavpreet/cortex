"""MCP server metrics for transport-level instrumentation.

Provides metrics for tracking MCP server operations including:
- Tool call counts and durations (by tool name and status)
- Tool call errors (by tool name and error type)
- Active session count
- OpenAPI spec refresh outcomes
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Histogram buckets for tool call duration (in seconds)
# MCP tool calls proxy through to the Matik API, so buckets cover the full
# round-trip including network + API processing time.
TOOL_CALL_DURATION_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)


class McpMetrics:
    """Metrics for MCP server transport-level instrumentation.

    Tracks tool call counts, durations, errors, active sessions, and
    OpenAPI spec refresh outcomes.

    Usage:
        metrics = McpMetrics(meter)

        # Time a tool call
        record = metrics.start_tool_call("mcp_get_correlation_group")
        try:
            result = await proxy.forward(tool, arguments)
            record("success")
        except Exception as e:
            record("error", type(e).__name__)
            raise

        # Update active session count
        metrics.set_active_sessions(len(active_sessions))

        # Record spec refresh outcome
        metrics.record_spec_refresh("success")   # tool set changed
        metrics.record_spec_refresh("unchanged") # no change
        metrics.record_spec_refresh("error")     # fetch failed
    """

    def __init__(self, meter: metrics.Meter, service: str = "mcp-server") -> None:
        """Initialize MCP metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service: Service name label value (default: "mcp-server")
        """
        self._service = service

        self._tool_calls = meter.create_counter(
            name="matik_mcp_tool_calls_total",
            description="Total number of MCP tool calls",
            unit="{call}",
        )

        self._tool_call_duration = meter.create_histogram(
            name="matik_mcp_tool_call_duration_seconds",
            description="End-to-end MCP tool call duration (MCP protocol to API response)",
            unit="s",
            explicit_bucket_boundaries_advisory=TOOL_CALL_DURATION_BUCKETS,
        )

        self._tool_call_errors = meter.create_counter(
            name="matik_mcp_tool_call_errors_total",
            description="Total number of MCP tool call errors",
            unit="{error}",
        )

        self._active_sessions = meter.create_gauge(
            name="matik_mcp_active_sessions",
            description="Current number of active MCP client sessions",
            unit="{session}",
        )

        self._spec_refreshes = meter.create_counter(
            name="matik_mcp_spec_refreshes_total",
            description="Total number of OpenAPI spec refresh attempts",
            unit="{refresh}",
        )

    def record_tool_call(
        self,
        tool_name: str,
        status: str,
        duration_seconds: float | None = None,
        error_type: str | None = None,
    ) -> None:
        """Record a completed tool call with timing and status.

        Args:
            tool_name: MCP tool name (operationId, e.g., "mcp_get_correlation_group")
            status: Outcome — "success" or "error"
            duration_seconds: End-to-end duration in seconds. Pass None to skip
                        the latency histogram — used for failures with no
                        meaningful timing (e.g. an unknown tool rejected before
                        any work is done). Recording a synthetic 0.0 would skew
                        the duration percentiles.
            error_type: Exception class name on error (e.g., "HTTPStatusError"),
                        or "unknown_tool" if the tool was not found in the registry.
                        Pass None for successful calls.
        """
        attrs: dict[str, Any] = {
            "service": self._service,
            "tool_name": tool_name,
            "status": status,
        }

        self._tool_calls.add(1, attrs)
        if duration_seconds is not None:
            self._tool_call_duration.record(duration_seconds, attrs)

        if error_type is not None:
            error_attrs: dict[str, Any] = {
                "service": self._service,
                "tool_name": tool_name,
                "error_type": error_type,
            }
            self._tool_call_errors.add(1, error_attrs)

        logger.debug(
            "recorded mcp tool call metric",
            tool_name=tool_name,
            status=status,
            duration_seconds=(
                round(duration_seconds, 3) if duration_seconds is not None else None
            ),
            error_type=error_type,
        )

    def start_tool_call(
        self,
        tool_name: str,
    ) -> Callable[..., None]:
        """Start timing a tool call and return a function to record completion.

        Usage:
            record = metrics.start_tool_call("mcp_get_correlation_group")
            try:
                raw = await asyncio.to_thread(proxy.forward, tool, arguments)
                record("success")
            except Exception as e:
                record("error", type(e).__name__)
                raise

        Args:
            tool_name: MCP tool name (operationId)

        Returns:
            A function to call when the tool call completes.
            Accepts (status, error_type=None).
        """
        start = time.perf_counter()

        def record(status: str, error_type: str | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_tool_call(tool_name, status, duration, error_type)

        return record

    def set_active_sessions(self, count: int, transport: str = "sse") -> None:
        """Set the current number of active MCP client sessions for a transport.

        Each transport ("sse" / "http") is counted independently by its caller,
        so a single client session is never reflected under more than one
        transport label. SSE sessions are tracked at the SSE transport boundary
        (connect/disconnect); HTTP sessions are tracked by the streamable-HTTP
        session manager.

        Args:
            count: Current number of active sessions for this transport
            transport: Transport type — "sse" or "http" (default: "sse")
        """
        self._active_sessions.set(
            count, {"service": self._service, "transport": transport}
        )

        logger.debug(
            "recorded mcp active sessions metric",
            service=self._service,
            transport=transport,
            count=count,
        )

    def record_spec_refresh(self, status: str) -> None:
        """Record an OpenAPI spec refresh outcome.

        Args:
            status: Outcome of the refresh:
                    "success"   — fetch succeeded and tool set changed
                    "unchanged" — fetch succeeded, no tool changes detected
                    "error"     — fetch failed (network error or parse failure)
        """
        self._spec_refreshes.add(
            1,
            {"service": self._service, "status": status},
        )

        logger.debug(
            "recorded mcp spec refresh metric",
            service=self._service,
            status=status,
        )
