"""IncidentIO API metrics for batch operation instrumentation.

Provides metrics for tracking IncidentIO API batch operations:
- Batch size distribution
- Entities affected counts
"""

from typing import Any

from opentelemetry import metrics

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


class IncidentIOAPIMetrics:
    """Metrics for IncidentIO API batch operation instrumentation.

    Tracks batch sizes and affected entities for IncidentIO API operations.

    Usage:
        metrics = IncidentIOAPIMetrics(meter, "api")

        # Record a batch operation
        metrics.record_batch(
            entity_type="incident",
            batch_size=100,
            affected_rows=98,
        )
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize IncidentIO API metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service (e.g., "api")
        """
        self._service_name = service_name

        self._batch_size = meter.create_histogram(
            name="matik_incidentio_api_batch_size",
            description="Batch size distribution for IncidentIO API operations",
            unit="{item}",
            explicit_bucket_boundaries_advisory=BATCH_SIZE_BUCKETS,
        )

        self._entities_affected = meter.create_counter(
            name="matik_incidentio_api_entities_affected_total",
            description="Total number of entities affected by IncidentIO API operations",
            unit="{entity}",
        )

    def record_batch(
        self,
        entity_type: str,
        batch_size: int,
        affected_rows: int,
    ) -> None:
        """Record a batch operation.

        Args:
            entity_type: Entity type (e.g., "incident", "tracker")
            batch_size: Number of items in the batch
            affected_rows: Number of rows actually affected
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "entity_type": entity_type,
        }

        self._batch_size.record(batch_size, attrs)
        self._entities_affected.add(affected_rows, attrs)
