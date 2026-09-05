"""Scribe service metrics for message processing instrumentation.

Provides metrics for tracking Scribe operations including:
- Message processing counts and durations
- Dead letter queue (DLQ) routing counts
- Exponential backoff retry counts
- Queue depth gauges
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for message processing latency (in seconds)
# Scribe writes are fast DB operations, so buckets are tuned for sub-second latency
MESSAGE_PROCESSING_BUCKETS = (
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
)


class ScribeMetrics:
    """Metrics for Scribe service message processing instrumentation.

    Tracks message counts, processing durations, DLQ routing, and backoff
    retries with labels for source type and message type.

    Usage:
        metrics = ScribeMetrics(meter)

        # Record a processed message
        metrics.record_message_processed(
            source_type="incidentio",
            message_type="base",
            status="success",
            duration_seconds=0.05,
        )

        # Or use the timing helper
        record = metrics.start_message("incidentio", "base")
        try:
            await handler.handle_base(body)
            record("success")
        except Exception as e:
            record("error")
            raise

        # Record DLQ routing on non-retryable errors
        metrics.record_dlq_message("incidentio", "MalformedMessageError")

        # Record backoff on retryable errors
        metrics.record_backoff("incidentio")

        # Update queue depth from SQS attribute polling
        metrics.set_queue_depth("scribe", 42)
        metrics.set_queue_depth("scribe-dlq", 0)
    """

    def __init__(self, meter: metrics.Meter, service_name: str = "scribe") -> None:
        """Initialize Scribe metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service (e.g., "scribe")
        """
        self._service_name = service_name
        self._messages_processed = meter.create_counter(
            name="matik_scribe_messages_processed_total",
            description="Total number of Scribe messages processed",
            unit="{message}",
        )

        self._message_duration = meter.create_histogram(
            name="matik_scribe_message_processing_duration_seconds",
            description="Time to process a single Scribe message end-to-end",
            unit="s",
            explicit_bucket_boundaries_advisory=MESSAGE_PROCESSING_BUCKETS,
        )

        self._dlq_messages = meter.create_counter(
            name="matik_scribe_dlq_messages_total",
            description="Total number of messages routed to the dead letter queue",
            unit="{message}",
        )

        self._backoff_total = meter.create_counter(
            name="matik_scribe_backoff_total",
            description="Total number of exponential backoff retries triggered",
            unit="{retry}",
        )

        self._queue_depth = meter.create_gauge(
            name="matik_scribe_queue_depth",
            description="Approximate number of messages currently in the queue",
            unit="{message}",
        )

        self._hook_total = meter.create_counter(
            name="matik_scribe_hook_total",
            description="Total number of post-write hook invocations",
            unit="{invocation}",
        )

        self._hook_duration = meter.create_histogram(
            name="matik_scribe_hook_duration_seconds",
            description="Time to execute a single post-write hook",
            unit="s",
            explicit_bucket_boundaries_advisory=MESSAGE_PROCESSING_BUCKETS,
        )

    def record_message_processed(
        self,
        source_type: str,
        message_type: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record a processed Scribe message with timing and status.

        Args:
            source_type: Data source (e.g., "incidentio", "ghe_pr", "jira", "correlation")
            message_type: Message type (e.g., "base", "enrichment")
            status: Outcome of processing (e.g., "success", "error")
            duration_seconds: Processing duration in seconds
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "message_type": message_type,
            "status": status,
        }

        self._messages_processed.add(1, attrs)
        self._message_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded scribe message processed metric",
            source_type=source_type,
            message_type=message_type,
            status=status,
            duration_seconds=round(duration_seconds, 3),
        )

    def record_dlq_message(
        self, source_type: str, error_type: str, redriven: bool = False
    ) -> None:
        """Record a message routed to the dead letter queue.

        Args:
            source_type: Data source (e.g., "incidentio", "ghe_pr", "jira", "correlation")
            error_type: Class name of the error that triggered DLQ routing
                        (e.g., "MalformedMessageError", "ValidationError")
            redriven: Whether this message was previously redriven from a DLQ
                      (carries a RedriveRunId message attribute) rather than a
                      fresh message
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "error_type": error_type,
            "redriven": "true" if redriven else "false",
        }

        self._dlq_messages.add(1, attrs)

        logger.debug(
            "recorded scribe DLQ message metric",
            source_type=source_type,
            error_type=error_type,
            redriven=redriven,
        )

    def record_backoff(self, source_type: str) -> None:
        """Record an exponential backoff retry triggered by a retryable error.

        Args:
            source_type: Data source (e.g., "incidentio", "ghe_pr", "jira", "correlation")
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
        }

        self._backoff_total.add(1, attrs)

        logger.debug(
            "recorded scribe backoff metric",
            source_type=source_type,
        )

    def set_queue_depth(self, queue_name: str, depth: int) -> None:
        """Set the current approximate message count for a queue.

        Called periodically by a background polling task. SQS values are
        approximate and suitable for monitoring and alerting purposes.

        Args:
            queue_name: Short queue identifier for the label
                        (e.g., "scribe", "scribe-dlq")
            depth: Approximate number of messages in the queue
        """
        attrs: dict[str, Any] = {"queue": queue_name}

        self._queue_depth.set(depth, attrs)

        logger.debug(
            "recorded scribe queue depth metric",
            queue=queue_name,
            depth=depth,
        )

    def record_hook(
        self,
        hook_name: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Record a post-write hook invocation with timing and status.

        Args:
            hook_name: Hook identifier (e.g., "enigmatologist_correlation")
            status: Outcome of the hook ("success" or "error")
            duration_seconds: Hook execution duration in seconds
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "hook_name": hook_name,
            "status": status,
        }
        self._hook_total.add(1, attrs)
        self._hook_duration.record(duration_seconds, attrs)

    def record_batch_flush(
        self,
        source_type: str,
        message_type: str,
        batch_size: int,
        duration_seconds: float,
        status: str,
        redriven: bool = False,
    ) -> None:
        """Record a completed batch flush with size, duration, and outcome.

        Args:
            source_type: Data source (e.g., "incidentio", "jira")
            message_type: Message type (e.g., "base", "enrichment")
            batch_size: Number of messages in the flush group
            duration_seconds: Total flush duration in seconds
            status: Outcome ("success" or "error")
            redriven: Whether this group contains at least one message that was
                      previously redriven from a DLQ (carries a RedriveRunId
                      message attribute) rather than a fresh message
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "message_type": message_type,
            "status": status,
            "redriven": "true" if redriven else "false",
        }
        self._messages_processed.add(batch_size, attrs)
        self._message_duration.record(duration_seconds, attrs)

    def record_buffer_size(self, source_type: str, size: int) -> None:
        """Record the number of messages in the in-process buffer at flush time.

        Args:
            source_type: Data source owning this flush group
            size: Buffer size at the moment of flush
        """
        attrs: dict[str, Any] = {"source_type": source_type}
        self._queue_depth.set(size, attrs)

    def record_group_failure(
        self,
        source_type: str,
        message_type: str,
        size: int,
        redriven: bool = False,
    ) -> None:
        """Record a transient group-level flush failure (DAO returned None).

        Args:
            source_type: Data source for the failed group
            message_type: Message type for the failed group
            size: Number of messages in the failed group
            redriven: Whether this group contains at least one message that was
                      previously redriven from a DLQ (carries a RedriveRunId
                      message attribute) rather than a fresh message
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "source_type": source_type,
            "message_type": message_type,
            "redriven": "true" if redriven else "false",
        }
        self._backoff_total.add(size, attrs)

        logger.warning(
            "recorded scribe group failure metric",
            source_type=source_type,
            message_type=message_type,
            size=size,
        )

    def start_message(
        self,
        source_type: str,
        message_type: str,
    ) -> Callable[[str], None]:
        """Start timing a message and return a function to record completion.

        Usage:
            record = metrics.start_message("incidentio", "base")
            try:
                await handler.handle_base(body)
                record("success")
            except Exception as e:
                record("error")
                raise

        Args:
            source_type: Data source identifier
            message_type: Message type identifier

        Returns:
            A function to call when processing completes, accepting the status string.
        """
        start = time.perf_counter()

        def record(status: str) -> None:
            duration = time.perf_counter() - start
            self.record_message_processed(source_type, message_type, status, duration)

        return record
