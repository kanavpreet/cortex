"""GHE API metrics for business-level operation instrumentation.

Provides metrics for tracking GHE API operations including:
- Operation counts by entity type and status
- Batch size distribution
- Entities affected counts
- Operation duration histograms
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for API operation latency (in seconds)
# Includes longer buckets for batch operations
API_LATENCY_BUCKETS = (
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

# Histogram buckets for batch sizes
BATCH_SIZE_BUCKETS = (
    1,
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
)


class GHEAPIMetrics:
    """Metrics for GHE API operation instrumentation.

    Tracks operation counts, durations, batch sizes, and affected entities
    with labels for service, operation type, entity type, and status.

    Usage:
        metrics = GHEAPIMetrics(meter, "api")

        # Record a single operation
        metrics.record_operation(
            operation="upsert",
            entity_type="organization",
            success=True,
            duration_seconds=0.5,
        )

        # Record a batch operation (duration tracked via start_operation)
        metrics.record_batch_operation(
            entity_type="pull_request",
            batch_size=100,
            affected_rows=98,
            success=True,
        )

        # Or use the context manager pattern
        record = metrics.start_operation("upsert", "repository")
        try:
            # do work
            record(success=True)
        except Exception:
            record(success=False)
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize GHE API metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service (e.g., "api")
        """
        self._service_name = service_name

        self._operation_count = meter.create_counter(
            name="matik_ghe_api_operations_total",
            description="Total number of GHE API operations",
            unit="{operation}",
        )

        self._operation_duration = meter.create_histogram(
            name="matik_ghe_api_operation_duration_seconds",
            description="GHE API operation duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=API_LATENCY_BUCKETS,
        )

        self._batch_size = meter.create_histogram(
            name="matik_ghe_api_batch_size",
            description="Batch size distribution for GHE API batch operations",
            unit="{item}",
            explicit_bucket_boundaries_advisory=BATCH_SIZE_BUCKETS,
        )

        self._entities_affected = meter.create_counter(
            name="matik_ghe_api_entities_affected_total",
            description="Total number of entities affected by GHE API operations",
            unit="{entity}",
        )

    def record_operation(
        self,
        operation: str,
        entity_type: str,
        success: bool,
        duration_seconds: float,
    ) -> None:
        """Record a GHE API operation.

        Args:
            operation: Operation type (e.g., "upsert", "get", "batch_upsert")
            entity_type: Entity type (e.g., "organization", "repository", "pull_request")
            success: Whether the operation was successful
            duration_seconds: Operation duration in seconds
        """
        status = "success" if success else "error"

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "operation": operation,
            "entity_type": entity_type,
            "status": status,
        }

        self._operation_count.add(1, attrs)
        self._operation_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded ghe api operation metric",
            operation=operation,
            entity_type=entity_type,
            status=status,
            duration_seconds=round(duration_seconds, 3),
        )

    def record_batch_operation(
        self,
        entity_type: str,
        batch_size: int,
        affected_rows: int,
        success: bool,
    ) -> None:
        """Record a batch GHE API operation.

        Note: Duration is tracked separately via start_operation(), so this method
        only records batch-specific metrics (batch_size, affected_rows).

        Args:
            entity_type: Entity type (e.g., "pull_request")
            batch_size: Number of items in the batch
            affected_rows: Number of rows actually affected
            success: Whether the operation was successful
        """
        # Record batch size
        batch_attrs: dict[str, Any] = {
            "service": self._service_name,
            "entity_type": entity_type,
        }
        self._batch_size.record(batch_size, batch_attrs)

        # Record entities affected (only on success)
        if success:
            affected_attrs: dict[str, Any] = {
                "service": self._service_name,
                "entity_type": entity_type,
                "operation": "batch_upsert",
            }
            self._entities_affected.add(affected_rows, affected_attrs)

        logger.debug(
            "recorded ghe api batch operation metric",
            entity_type=entity_type,
            batch_size=batch_size,
            affected_rows=affected_rows,
            success=success,
        )

    def start_operation(
        self,
        operation: str,
        entity_type: str,
    ) -> Callable[[bool], None]:
        """Start timing an operation and return a function to record completion.

        Usage:
            record = metrics.start_operation("upsert", "organization")
            try:
                # do work
                record(success=True)
            except Exception:
                record(success=False)
                raise

        Args:
            operation: Operation type (e.g., "upsert", "get")
            entity_type: Entity type (e.g., "organization", "repository")

        Returns:
            A function to call when the operation completes with success status.
        """
        start = time.perf_counter()

        def record(success: bool) -> None:
            duration = time.perf_counter() - start
            self.record_operation(operation, entity_type, success, duration)

        return record
