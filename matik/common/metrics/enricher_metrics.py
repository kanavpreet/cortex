"""Enricher service metrics for tracking LLM enrichment processing.

Provides OpenTelemetry instruments for the Enricher service. All instruments
use the ``matik_enricher_`` prefix for easy namespace filtering in Prometheus.

Alerting rules (document as comments for SRE reference):

  # DLQ has any messages — investigate immediately
  ALERT EnricherDLQDepth
    IF matik_enricher_queue_depth{queue="dlq"} > 0

  # High DLQ ingestion rate — non-retryable errors flooding in
  ALERT EnricherDLQRate
    IF rate(matik_enricher_dlq_messages_total[5m]) > 1

  # Consumer falling behind — enricher queue depth growing
  ALERT EnricherQueueDepthHigh
    IF matik_enricher_queue_depth{queue="enricher"} > 1000

  # Facade under pressure — many backoffs happening
  ALERT EnricherBackoffPressure
    IF rate(matik_enricher_backoff_total[5m]) > 10
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for LLM enrichment duration (in seconds)
ENRICHER_PROCESSING_DURATION_BUCKETS = (
    1.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
)


class EnricherMetrics:
    """Metrics instruments for the Enricher service.

    Tracks message processing counts, durations, DLQ events, backoff events,
    and queue depth for both the enricher input queue and DLQ.

    Usage::

        metrics = EnricherMetrics(meter, "enricher")

        record = metrics.start_message("incidentio")
        try:
            await handler.enrich(request)
            record(True)
        except Exception as e:
            record(False, e)
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize Enricher metrics instruments.

        Args:
            meter: OpenTelemetry meter for creating instruments.
            service_name: Name of the service (e.g., "enricher").
        """
        self._service_name = service_name

        # Counter: total messages processed (success or failure)
        self._messages_processed = meter.create_counter(
            name="matik_enricher_messages_processed_total",
            description=(
                "Total number of enrichment messages processed, "
                "labeled by source_type and status."
            ),
            unit="{message}",
        )

        # Histogram: end-to-end processing duration per message
        self._processing_duration = meter.create_histogram(
            name="matik_enricher_message_processing_duration_seconds",
            description=(
                "End-to-end enrichment message processing duration in seconds, "
                "labeled by source_type and status."
            ),
            unit="s",
            explicit_bucket_boundaries_advisory=ENRICHER_PROCESSING_DURATION_BUCKETS,
        )

        # Counter: per-operation enrichment outcomes (one Facade call = one operation)
        self._enrichment_operations = meter.create_counter(
            name="matik_enricher_enrichment_operations_total",
            description=(
                "Total number of individual LLM enrichment operations performed, "
                "labeled by source_type, enrichment_type, and status."
            ),
            unit="{operation}",
        )

        # Counter: messages sent to DLQ (non-retryable failures)
        self._dlq_messages = meter.create_counter(
            name="matik_enricher_dlq_messages_total",
            description=(
                "Total number of enrichment messages sent to the dead-letter queue, "
                "labeled by source_type and error_type."
            ),
            unit="{message}",
        )

        # Counter: backoff events (retryable failures)
        self._backoff_total = meter.create_counter(
            name="matik_enricher_backoff_total",
            description=(
                "Total number of enrichment message retry backoff events, "
                "labeled by source_type and retry_attempt."
            ),
            unit="{event}",
        )

        # Counter: hash cache operations (hit/miss/evict)
        self._hash_cache_ops = meter.create_counter(
            name="matik_enricher_hash_cache_operations_total",
            description="Hash cache operations by result (hit, miss, evict).",
            unit="{operation}",
        )

        # Counter: enrichment hash-dedup outcomes (hit/miss/new)
        self._enrichment_cache_ops = meter.create_counter(
            name="matik_enricher_enrichment_cache_operations_total",
            description=(
                "Enrichment hash-dedup outcomes by result: 'hit' (content "
                "unchanged, Facade/LLM call skipped), 'miss' (content changed), "
                "'new' (entity field seen for the first time)."
            ),
            unit="{operation}",
        )

        # Gauge: current queue depth (polled periodically)
        self._queue_depth = meter.create_gauge(
            name="matik_enricher_queue_depth",
            description=(
                "Approximate number of messages currently in the enricher or DLQ queue, "
                "labeled by queue ('enricher' or 'dlq')."
            ),
            unit="{message}",
        )

    def record_processed(
        self,
        source_type: str,
        duration_seconds: float,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        """Record completion of a single enrichment message.

        Args:
            source_type: The data source type (e.g., "incidentio").
            duration_seconds: Total wall-clock time for the message.
            success: True if the message was successfully enriched and deleted.
            error: Exception that caused failure, if any.
        """
        status = "success" if success else "failed"
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "status": status,
        }
        self._messages_processed.add(1, attrs)
        self._processing_duration.record(duration_seconds, attrs)

    def record_enrichment_operation(
        self,
        source_type: str,
        operation: str,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        """Record the outcome of a single Facade LLM call for one mapping entry.

        Called once per mapping entry inside the handler's ``_call_facade``.
        Provides the per-enrichment-type breakdown that the message-level
        ``record_processed`` cannot supply (a single message may contain N operations).

        Args:
            source_type: The data source type (e.g., "incidentio").
            operation: The enrichment operation name, equal to output_field
                (e.g., "root_cause_summary", "resolution_summary").
            success: True if the Facade call succeeded and produced output.
            error: Exception that caused failure, if any.
        """
        status = "success" if success else "failed"
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "enrichment_type": operation,
            "status": status,
        }
        self._enrichment_operations.add(1, attrs)

    def record_hash_cache(self, result: str, count: int = 1) -> None:
        """Record a hash cache operation outcome.

        Args:
            result: One of 'hit', 'miss', or 'evict'.
            count: Number of operations to record (default 1).
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "result": result,
        }
        self._hash_cache_ops.add(count, attrs)

    def record_enrichment_cache(
        self,
        source_type: str,
        enrichment_type: str,
        result: str,
        count: int = 1,
    ) -> None:
        """Record the hash-dedup outcome for one enrichment mapping.

        Called once per mapping entry in the handler's enrich loop. A 'hit'
        means the content hash matched the stored hash and the Facade/LLM call
        was skipped (a saved call); 'miss' means the content changed; 'new'
        means no prior hash existed for this field.

        Args:
            source_type: The data source type (e.g., "ghe_pr", "jira").
            enrichment_type: The enrichment operation name, equal to output_field
                (e.g., "pull_request_summary", "issue_summary").
            result: One of 'hit', 'miss', or 'new'.
            count: Number of operations to record (default 1).
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "enrichment_type": enrichment_type,
            "result": result,
        }
        self._enrichment_cache_ops.add(count, attrs)

    def record_dlq(
        self,
        source_type: str,
        error: Exception | None = None,
    ) -> None:
        """Record a message being sent to the dead-letter queue.

        Args:
            source_type: The data source type (e.g., "incidentio").
            error: The non-retryable exception that caused DLQ routing.
        """
        error_type = type(error).__name__ if error else "unknown"
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "error_type": error_type,
        }
        self._dlq_messages.add(1, attrs)

    def record_backoff(
        self,
        source_type: str,
        retry_attempt: int,
    ) -> None:
        """Record a retry backoff event for a retryable failure.

        Args:
            source_type: The data source type (e.g., "incidentio").
            retry_attempt: The current retry attempt number (1-based).
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "retry_attempt": str(retry_attempt),
        }
        self._backoff_total.add(1, attrs)

    def record_queue_depth(self, queue: str, depth: int) -> None:
        """Record the current approximate depth of a queue.

        Args:
            queue: Queue label — "enricher" for the input queue, "dlq" for DLQ.
            depth: Approximate number of messages in the queue.
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "queue": queue,
        }
        self._queue_depth.set(depth, attrs)

    def start_message(
        self,
        source_type: str,
    ) -> Callable[[bool, Exception | None], None]:
        """Start timing a message and return a recorder function.

        Usage::

            record = metrics.start_message("incidentio")
            try:
                await process(message)
                record(True, None)
            except Exception as e:
                record(False, e)

        Args:
            source_type: The data source type (e.g., "incidentio").

        Returns:
            A callable that records the outcome when called with (success, error).
        """
        start = time.perf_counter()

        def record(success: bool = True, error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_processed(source_type, duration, success, error)

        return record
