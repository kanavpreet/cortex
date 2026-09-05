"""SQS publisher metrics for tracking message publishing.

Provides metrics for any service that publishes to SQS including:
- Messages published (success/failed)
- Publish duration
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for SQS publish duration (in seconds)
# Smaller than consumer buckets — publishing is a single network call
SQS_PUBLISH_DURATION_BUCKETS = (
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


class SQSPublisherMetrics:
    """Metrics for SQS publisher instrumentation.

    Tracks publish counts and durations. Reusable across any service
    that uses SQSPublisher (historians, etc.).

    Usage:
        metrics = SQSPublisherMetrics(meter, "historian-jira")

        record = metrics.start_publish("matik-scribe-queue", "JiraBaseMessage")
        try:
            publisher.send_sync(message)
            record(success=True)
        except Exception as e:
            record(success=False, error=e)
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize SQS publisher metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service publishing to SQS
        """
        self._service_name = service_name

        self._messages_published = meter.create_counter(
            name="matik_sqs_messages_published_total",
            description="Total number of SQS messages successfully published",
            unit="{message}",
        )

        self._messages_publish_failed = meter.create_counter(
            name="matik_sqs_messages_publish_failed_total",
            description="Total number of SQS messages that failed to publish",
            unit="{message}",
        )

        self._publish_duration = meter.create_histogram(
            name="matik_sqs_publish_duration_seconds",
            description="SQS message publish duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=SQS_PUBLISH_DURATION_BUCKETS,
        )

    def record_publish(
        self,
        queue_name: str,
        message_type: str,
        duration_seconds: float,
        success: bool,
        error: Exception | None = None,
    ) -> None:
        """Record the outcome of publishing a single SQS message.

        Args:
            queue_name: SQS queue name
            message_type: Pydantic model class name of the published message
            duration_seconds: Time taken to publish the message
            success: True if message was published successfully
            error: Exception if publishing failed
        """
        status = "success" if success else "failed"
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "queue_name": queue_name,
            "message_type": message_type,
            "status": status,
        }

        self._publish_duration.record(duration_seconds, attrs)

        if success:
            self._messages_published.add(1, attrs)
        else:
            error_type = type(error).__name__ if error else "unknown"
            self._messages_publish_failed.add(1, {**attrs, "error_type": error_type})

    def start_publish(
        self,
        queue_name: str,
        message_type: str,
    ) -> Callable[[bool, Exception | None], None]:
        """Start timing a publish and return a function to record completion.

        Usage:
            record = metrics.start_publish("matik-scribe-queue", "JiraBaseMessage")
            try:
                publisher.send_sync(message)
                record(success=True)
            except Exception as e:
                record(success=False, error=e)

        Args:
            queue_name: SQS queue name
            message_type: Pydantic model class name of the message being published

        Returns:
            A function to call on completion with (success, error).
        """
        start = time.perf_counter()

        def record(success: bool = True, error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_publish(queue_name, message_type, duration, success, error)

        return record
