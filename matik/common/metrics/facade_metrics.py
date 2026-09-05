"""Facade (LLM) metrics for tracking API calls and token usage.

Provides metrics for tracking Facade/LLM operations including:
- Call counts by model and operation
- Token usage (prompt, completion, total)
- Call duration
- Rate limit gauges from Facade response headers
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for LLM call latency (in seconds)
# LLM calls can be slow, so include longer buckets
LLM_LATENCY_BUCKETS = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
)


class FacadeMetrics:
    """Metrics for Facade/LLM API instrumentation.

    Tracks call counts, token usage, and durations with labels for
    service, model, and operation type.

    Usage:
        metrics = FacadeMetrics(meter, "historian-incidentio", "facade")

        # Record a call with token usage
        metrics.record_call(
            model="gpt-4o",
            operation="summarize_root_cause",
            prompt_tokens=500,
            completion_tokens=100,
            duration_seconds=2.5,
        )

        # Or use timing helper
        record = metrics.start_call("gpt-4o", "summarize_root_cause")
        response = await facade.send_message(...)
        record(prompt_tokens=500, completion_tokens=100)
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
        client_name: str,
        track_rate_limits: bool = True,
    ) -> None:
        """Initialize Facade metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Full service name (e.g., "historian-jira", "historian-incidentio")
            client_name: LLM client type (e.g., "facade", "bedrock")
            track_rate_limits: Whether to register rate limit gauges. Set to False when
                the LLM gateway does not return x-ratelimit-* response headers (e.g.,
                enigmatologist uses Facade without rate limit header support).
        """
        self._service_name = service_name
        self._client_name = client_name
        self._track_rate_limits = track_rate_limits

        self._call_count = meter.create_counter(
            name="matik_facade_calls_total",
            description="Total number of Facade/LLM API calls",
            unit="{call}",
        )

        self._call_duration = meter.create_histogram(
            name="matik_facade_call_duration_seconds",
            description="Facade/LLM API call duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=LLM_LATENCY_BUCKETS,
        )

        self._prompt_tokens = meter.create_counter(
            name="matik_facade_prompt_tokens_total",
            description="Total prompt tokens used in Facade/LLM calls",
            unit="{token}",
        )

        self._completion_tokens = meter.create_counter(
            name="matik_facade_completion_tokens_total",
            description="Total completion tokens used in Facade/LLM calls",
            unit="{token}",
        )

        self._call_errors = meter.create_counter(
            name="matik_facade_call_errors_total",
            description="Total number of Facade/LLM API call errors",
            unit="{error}",
        )

        # Rate limit gauges — only created when track_rate_limits=True
        self._ratelimit_limit_requests: Any | None
        self._ratelimit_remaining_requests: Any | None
        self._ratelimit_limit_tokens: Any | None
        self._ratelimit_remaining_tokens: Any | None
        if track_rate_limits:
            self._ratelimit_limit_requests = meter.create_gauge(
                name="matik_facade_ratelimit_limit_requests",
                description="Facade rate limit: max requests allowed",
                unit="{request}",
            )
            self._ratelimit_remaining_requests = meter.create_gauge(
                name="matik_facade_ratelimit_remaining_requests",
                description="Facade rate limit: remaining requests",
                unit="{request}",
            )
            self._ratelimit_limit_tokens = meter.create_gauge(
                name="matik_facade_ratelimit_limit_tokens",
                description="Facade rate limit: max tokens allowed",
                unit="{token}",
            )
            self._ratelimit_remaining_tokens = meter.create_gauge(
                name="matik_facade_ratelimit_remaining_tokens",
                description="Facade rate limit: remaining tokens",
                unit="{token}",
            )
        else:
            self._ratelimit_limit_requests = None
            self._ratelimit_remaining_requests = None
            self._ratelimit_limit_tokens = None
            self._ratelimit_remaining_tokens = None

    def record_call(
        self,
        model: str,
        operation: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_seconds: float = 0,
        error: bool = False,
    ) -> None:
        """Record a Facade/LLM API call.

        Args:
            model: Model used (e.g., "gpt-4o")
            operation: Operation type (e.g., "summarize_root_cause", "summarize_resolution")
            prompt_tokens: Number of prompt tokens used
            completion_tokens: Number of completion tokens used
            duration_seconds: Call duration in seconds
            error: Whether the call resulted in an error
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "client": self._client_name,
            "model": model,
            "operation": operation,
        }

        self._call_count.add(1, attrs)
        self._call_duration.record(duration_seconds, attrs)

        if error:
            self._call_errors.add(1, attrs)

        if prompt_tokens > 0:
            self._prompt_tokens.add(prompt_tokens, attrs)

        if completion_tokens > 0:
            self._completion_tokens.add(completion_tokens, attrs)

    def start_call(
        self,
        model: str,
        operation: str,
    ) -> Callable[[int, int, bool], None]:
        """Start timing a call and return a function to record completion.

        Usage:
            record = metrics.start_call("gpt-4o", "summarize_root_cause")
            try:
                response, usage = await facade.send_message(...)
                record(usage.prompt_tokens, usage.completion_tokens, False)
            except Exception:
                record(0, 0, True)

        Args:
            model: Model used
            operation: Operation type

        Returns:
            A function to call when complete with (prompt_tokens, completion_tokens, error).
        """
        start = time.perf_counter()

        def record(
            prompt_tokens: int = 0, completion_tokens: int = 0, error: bool = False
        ) -> None:
            duration = time.perf_counter() - start
            self.record_call(
                model, operation, prompt_tokens, completion_tokens, duration, error
            )

        return record

    def record_rate_limits(
        self,
        headers: Mapping[str, str],
        model: str,
        operation: str,
    ) -> None:
        """Record rate limit information from Facade response headers.

        Extracts the following headers and sets corresponding gauges:
        - x-ratelimit-limit-requests
        - x-ratelimit-remaining-requests
        - x-ratelimit-limit-tokens
        - x-ratelimit-remaining-tokens

        Missing or non-numeric header values are silently ignored.
        No-op when track_rate_limits=False.

        Args:
            headers: Response headers (dict-like mapping)
            model: Model used (e.g., "gpt-4o")
            operation: Operation type (e.g., "summarize_root_cause")
        """
        if not self._track_rate_limits:
            return

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "client": self._client_name,
            "model": model,
            "operation": operation,
        }

        header_gauge_map = {
            "x-ratelimit-limit-requests": self._ratelimit_limit_requests,
            "x-ratelimit-remaining-requests": self._ratelimit_remaining_requests,
            "x-ratelimit-limit-tokens": self._ratelimit_limit_tokens,
            "x-ratelimit-remaining-tokens": self._ratelimit_remaining_tokens,
        }

        for header_name, gauge in header_gauge_map.items():
            value = headers.get(header_name)
            if value is not None and gauge is not None:
                try:
                    gauge.set(int(value), attrs)
                except (ValueError, TypeError):
                    logger.debug(
                        "Non-numeric rate limit header %s: %s",
                        header_name,
                        value,
                    )
