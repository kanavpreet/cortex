"""Job metrics for background task instrumentation.

Provides metrics for tracking background jobs including:
- Job execution counts and durations
- Error tracking (job failures and errors encountered)
- Items processed counts
- Last run timestamps
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for job duration (in seconds)
# Jobs can run for longer periods, so we include larger buckets
JOB_DURATION_BUCKETS = (
    1.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    3600.0,
)


class JobMetrics:
    """Metrics for background job instrumentation.

    Tracks job executions, durations, errors, and items processed
    with labels for service, connector type, and status.

    Usage:
        metrics = JobMetrics(meter, "historian")

        # Record a job execution
        metrics.record_job_execution(
            connector_type="incidentio_incidents",
            duration_seconds=120.0,
            item_count=42,
        )

        # Or use the context manager
        record = metrics.start_job("incidentio_incidents")
        try:
            items = await process_incidents()
            record(len(items), None)
        except Exception as e:
            record(0, e)

        # Record non-fatal errors during processing
        metrics.record_job_error("incidentio_incidents", "rate_limit")
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize job metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service running jobs
        """
        self._service_name = service_name

        self._job_executions = meter.create_counter(
            name="matik_job_executions_total",
            description="Total number of background job executions",
            unit="{execution}",
        )

        self._job_duration = meter.create_histogram(
            name="matik_job_duration_seconds",
            description="Background job duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=JOB_DURATION_BUCKETS,
        )

        self._job_errors = meter.create_counter(
            name="matik_job_errors_total",
            description="Total number of background job errors",
            unit="{error}",
        )

        self._job_errors_encountered = meter.create_counter(
            name="matik_job_errors_encountered_total",
            description="Total number of errors encountered during job execution "
            "(job may still succeed)",
            unit="{error}",
        )

        self._items_processed = meter.create_counter(
            name="matik_job_items_processed_total",
            description="Total number of items processed by background jobs",
            unit="{item}",
        )

        self._last_run_timestamp = meter.create_gauge(
            name="matik_job_last_run_timestamp",
            description="Unix timestamp of the last job run",
            unit="s",
        )

    def record_job_execution(
        self,
        connector_type: str,
        duration_seconds: float,
        item_count: int = 0,
        error: Exception | None = None,
    ) -> None:
        """Record a background job execution with timing and error tracking.

        Args:
            connector_type: Type of connector/job (e.g., "incidentio_incidents")
            duration_seconds: Job duration in seconds
            item_count: Number of items processed
            error: Exception if the job failed
        """
        status = "error" if error is not None else "success"

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "connector_type": connector_type,
            "status": status,
        }

        self._job_executions.add(1, attrs)
        self._job_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded job execution metric",
            connector_type=connector_type,
            status=status,
            duration_seconds=round(duration_seconds, 3),
            item_count=item_count,
        )

        if error is not None:
            self._job_errors.add(1, attrs)
            logger.debug(
                "recorded job error metric",
                connector_type=connector_type,
                error_type=type(error).__name__,
            )

        self._items_processed.add(item_count, attrs)

        # Record the last run timestamp
        timestamp_attrs = {
            "service": self._service_name,
            "connector_type": connector_type,
        }
        self._last_run_timestamp.set(int(time.time()), timestamp_attrs)

    def record_items_processed(
        self,
        connector_type: str,
        count: int,
    ) -> None:
        """Record the number of items processed in a job.

        Args:
            connector_type: Type of connector/job
            count: Number of items processed
        """
        if count <= 0:
            return

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "connector_type": connector_type,
        }

        self._items_processed.add(count, attrs)

    def record_job_error(
        self,
        connector_type: str,
        error_type: str,
    ) -> None:
        """Record an error encountered during job execution without failing the job.

        This is useful for tracking errors like rate limits where the job
        continues processing other items but should still report the error
        occurrence.

        Args:
            connector_type: Type of connector/job
            error_type: Short identifier like "rate_limit", "api_error", "parse_error"
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "connector_type": connector_type,
            "error_type": error_type,
        }

        self._job_errors_encountered.add(1, attrs)
        logger.debug(
            "recorded job error encountered metric",
            connector_type=connector_type,
            error_type=error_type,
        )

    def start_job(
        self,
        connector_type: str,
    ) -> Callable[[int, Exception | None], None]:
        """Start timing a job and return a function to record completion.

        Usage:
            record = metrics.start_job("incidentio_incidents")
            try:
                items = await process_incidents()
                record(len(items), None)
            except Exception as e:
                record(0, e)
                raise

        Args:
            connector_type: Type of connector/job

        Returns:
            A function to call when the job completes with item_count and error.
        """
        start = time.perf_counter()

        def record(item_count: int = 0, error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_job_execution(connector_type, duration, item_count, error)

        return record
