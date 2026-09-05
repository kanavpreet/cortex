"""Chronicler service metrics for the Kafka consumer loop.

Provides OpenTelemetry instruments for the Chronicler service. All instruments
use the ``matik_chronicler_`` prefix for easy namespace filtering in Prometheus.

Alerting rules (document as comments for SRE reference):

  # Chronicler dropping events because signature validation failed
  ALERT ChroniclerInvalidSignatures
    IF rate(matik_chronicler_signature_validations_total{result="invalid"}[5m]) > 0

  # Scribe / Enricher SQS publishes failing repeatedly
  ALERT ChroniclerPublishFailures
    IF rate(matik_chronicler_publish_outcome_total{status="failed"}[5m]) > 0.1

  # Consumer not making progress (no messages received in 30m)
  ALERT ChroniclerStalled
    IF rate(matik_chronicler_messages_received_total[30m]) == 0

  # Poll loop is unhealthy — librdkafka errors
  ALERT ChroniclerKafkaErrors
    IF rate(matik_chronicler_kafka_errors_total[5m]) > 0.1
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Histogram buckets for end-to-end per-message processing (seconds). Mirrors
# the shape used by EnricherMetrics — most chronicler messages clear in tens
# of ms but Scribe/Enricher SQS round trips can extend into single-digit seconds.
CHRONICLER_PROCESSING_DURATION_BUCKETS = (
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

# Smaller buckets for the librdkafka poll itself — almost always sub-second.
CHRONICLER_POLL_DURATION_BUCKETS = (
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)


class ChroniclerMetrics:
    """Metrics instruments for the Chronicler Kafka consumer.

    Tracks ingest rate, per-provider transform outcomes, end-to-end latency,
    signature-validation results, downstream SQS publish health, and Kafka
    poll-loop liveness.

    Usage::

        metrics = ChroniclerMetrics(meter, "chronicler")

        record = metrics.start_message(topic)
        try:
            ... process ...
            record(provider, "success")
        except Exception:
            record(provider, "error")
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str = "chronicler",
    ) -> None:
        self._service_name = service_name

        self._messages_received = meter.create_counter(
            name="matik_chronicler_messages_received_total",
            description=(
                "Total number of Kafka messages received from Yoyo callback "
                "topics, labeled by topic."
            ),
            unit="{message}",
        )

        self._messages_processed = meter.create_counter(
            name="matik_chronicler_messages_processed_total",
            description=(
                "Total number of Kafka messages processed end-to-end, labeled "
                "by topic, provider, and terminal status (success, filtered, "
                "signature_failed, unknown_provider, malformed_envelope, "
                "malformed_payload, error)."
            ),
            unit="{message}",
        )

        self._processing_duration = meter.create_histogram(
            name="matik_chronicler_message_processing_duration_seconds",
            description=(
                "End-to-end per-message wall-clock time inside the consumer "
                "loop, labeled by topic, provider, and terminal status."
            ),
            unit="s",
            explicit_bucket_boundaries_advisory=CHRONICLER_PROCESSING_DURATION_BUCKETS,
        )

        self._signature_validations = meter.create_counter(
            name="matik_chronicler_signature_validations_total",
            description=(
                "HMAC / Svix signature validation outcomes, labeled by "
                "provider and result (valid, invalid, skipped_no_secret, "
                "skipped_no_scheme)."
            ),
            unit="{validation}",
        )

        self._publish_outcome = meter.create_counter(
            name="matik_chronicler_publish_outcome_total",
            description=(
                "Outcomes of outbound SQS publishes from the consumer loop, "
                "labeled by target (scribe, enricher), provider, and status "
                "(success, failed)."
            ),
            unit="{publish}",
        )

        self._poll_duration = meter.create_histogram(
            name="matik_chronicler_kafka_poll_duration_seconds",
            description="librdkafka consumer.poll() wall-clock time in seconds.",
            unit="s",
            explicit_bucket_boundaries_advisory=CHRONICLER_POLL_DURATION_BUCKETS,
        )

        self._kafka_errors = meter.create_counter(
            name="matik_chronicler_kafka_errors_total",
            description=(
                "Kafka-level errors surfaced on consumed messages, labeled "
                "by error_type."
            ),
            unit="{error}",
        )

    def record_received(self, topic: str) -> None:
        attrs: dict[str, Any] = {"service": self._service_name, "topic": topic}
        self._messages_received.add(1, attrs)

    def record_processed(
        self,
        topic: str,
        provider: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "topic": topic,
            "provider": provider,
            "status": status,
        }
        self._messages_processed.add(1, attrs)
        self._processing_duration.record(duration_seconds, attrs)

    def record_signature(self, provider: str, result: str) -> None:
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "provider": provider,
            "result": result,
        }
        self._signature_validations.add(1, attrs)

    def record_publish(self, target: str, provider: str, success: bool) -> None:
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "target": target,
            "provider": provider,
            "status": "success" if success else "failed",
        }
        self._publish_outcome.add(1, attrs)

    def record_kafka_poll(self, duration_seconds: float) -> None:
        self._poll_duration.record(duration_seconds, {"service": self._service_name})

    def record_kafka_error(self, error_type: str) -> None:
        self._kafka_errors.add(
            1, {"service": self._service_name, "error_type": error_type}
        )

    def start_message(self, topic: str) -> Callable[[str, str], None]:
        """Start timing a Kafka message and return a recorder.

        Usage::

            record = metrics.start_message(topic)
            ... process ...
            record(provider, "success")

        Args:
            topic: The Kafka topic the record came from.

        Returns:
            A callable ``record(provider, status)`` that captures the elapsed
            wall-clock time and emits both the processed counter and the
            duration histogram with the same label set.
        """
        start = time.perf_counter()

        def record(provider: str, status: str) -> None:
            duration = time.perf_counter() - start
            self.record_processed(topic, provider, status, duration)

        return record
