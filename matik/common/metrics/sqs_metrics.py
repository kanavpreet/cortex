"""SQS worker metrics for tracking message processing.

Provides metrics for any SQS-based worker including:
- Messages received and processed
- Messages failed (returned to queue)
- Processing duration
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for SQS message processing duration (in seconds)
SQS_PROCESSING_DURATION_BUCKETS = (
    0.5,
    1.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
)


class SQSMetrics:
    """Metrics for SQS worker instrumentation.

    Tracks message processing counts and durations. Reusable across any
    SQS-based worker service (enigmatologist, chronicler, etc.).

    Usage:
        metrics = SQSMetrics(meter, "enigmatologist")

        record = metrics.start_message("matik-enig-triggers-sandbox-queue")
        try:
            await process(message)
            record(success=True)
        except Exception as e:
            record(success=False, error=e)
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize SQS metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service consuming from SQS
        """
        self._service_name = service_name

        self._messages_processed = meter.create_counter(
            name="matik_sqs_processed_messages_total",
            description="Total number of SQS messages successfully processed and deleted",
            unit="{message}",
        )

        self._messages_failed = meter.create_counter(
            name="matik_sqs_failed_messages_total",
            description="Total number of SQS messages that failed and were returned to queue",
            unit="{message}",
        )

        self._poll_total = meter.create_counter(
            name="matik_sqs_poll_total",
            description="Total number of SQS poll attempts, labeled by queue_name. "
            "Always increments on every poll cycle, keeping the series alive in "
            "Prometheus even when no messages are received.",
            unit="{poll}",
        )

        self._message_retries = meter.create_counter(
            name="matik_sqs_message_retries_total",
            description="Total number of redelivered SQS messages (ApproximateReceiveCount "
            "> 1), labeled by queue_name. Surfaces native SQS redrive/backoff: a message "
            "left on the queue after a failure is redelivered and eventually moved to the "
            "DLQ once maxReceiveCount is exceeded.",
            unit="{message}",
        )

        self._processing_duration = meter.create_histogram(
            name="matik_sqs_processing_seconds",
            description="SQS message processing duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=SQS_PROCESSING_DURATION_BUCKETS,
        )

    def record_poll(self, queue_name: str) -> None:
        """Record a single SQS poll attempt.

        Call this on every poll cycle regardless of whether messages were
        received. Keeps the time series alive in Prometheus so that
        rate(matik_sqs_processed_messages_total[...]) returns 0 during idle
        periods instead of no data.

        Args:
            queue_name: SQS queue name being polled.
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "queue_name": queue_name,
        }
        self._poll_total.add(1, attrs)

    def record_retry(self, queue_name: str, receive_count: int) -> None:
        """Record an SQS message redelivery (retry) based on its receive count.

        Call this for each received message using its ``ApproximateReceiveCount``
        attribute. The first delivery (count == 1) is not a retry and is ignored;
        any higher count means the message was previously left on the queue after
        a failure and redelivered. This is the real "backoff" signal under native
        SQS redrive, where retries are managed by the queue rather than in-process.

        Args:
            queue_name: SQS queue name being polled.
            receive_count: Value of the message's ApproximateReceiveCount attribute.
        """
        if receive_count <= 1:
            return
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "queue_name": queue_name,
        }
        self._message_retries.add(1, attrs)

    def record_processed(
        self,
        queue_name: str,
        duration_seconds: float,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        """Record the outcome of processing a single SQS message.

        Args:
            queue_name: SQS queue name
            duration_seconds: Time taken to process the message
            success: True if message was deleted (success), False if returned to queue
            error: Exception if processing failed
        """
        status = "success" if success else "failed"
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "queue_name": queue_name,
            "status": status,
        }

        self._processing_duration.record(duration_seconds, attrs)

        if success:
            self._messages_processed.add(1, attrs)
        else:
            error_type = type(error).__name__ if error else "unknown"
            self._messages_failed.add(1, {**attrs, "error_type": error_type})

    def start_message(
        self,
        queue_name: str,
    ) -> Callable[[bool, Exception | None], None]:
        """Start timing a message and return a function to record completion.

        Usage:
            record = metrics.start_message("matik-enig-triggers-sandbox-queue")
            try:
                await process(message)
                record(success=True)
            except Exception as e:
                record(success=False, error=e)

        Args:
            queue_name: SQS queue name

        Returns:
            A function to call on completion with (success, error).
        """
        start = time.perf_counter()

        def record(success: bool = True, error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_processed(queue_name, duration, success, error)

        return record
