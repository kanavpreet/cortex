"""Data Access Object for reliability_correlations table."""

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, select, text

from common.models.reliability_correlation import ReliabilityCorrelation
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics


def _to_json(value: list[Any] | None) -> str | None:
    """Serialize list to JSON string for MySQL JSON columns."""
    if value is None:
        return None
    return json.dumps(value)


logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
reliability_correlations_table = (
    ReliabilityCorrelation.__table__  # type: ignore[attr-defined]
)

# MySQL packet size safe limit
BATCH_SIZE = 500


class ReliabilityCorrelationDAO:
    """DAO for reliability_correlations table with batch operations."""

    def __init__(self, engine: Engine, metrics: "DBMetrics | None" = None) -> None:
        """Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        self._engine = engine
        self._metrics = metrics

    def find_by_anchor(self, anchor_entity_id: str) -> list[ReliabilityCorrelation]:
        """Find all correlations for a given anchor entity.

        Args:
            anchor_entity_id: The anchor entity ID (e.g. INC-99)

        Returns:
            List of ReliabilityCorrelation models, empty list on error
        """
        record = (
            self._metrics.start_query("select", "reliability_correlations")
            if self._metrics
            else None
        )
        try:
            stmt = select(ReliabilityCorrelation).where(
                reliability_correlations_table.c.anchor_entity_id == anchor_entity_id
            )
            with self._engine.connect() as conn:
                rows = conn.execute(stmt).fetchall()

            if record:
                record(None)

            return [
                ReliabilityCorrelation.model_validate(row, from_attributes=True)
                for row in rows
            ]
        except Exception as e:
            logger.exception(
                "error finding correlations by anchor",
                anchor_entity_id=anchor_entity_id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return []

    def upsert_correlations_batch(
        self, correlations: list[ReliabilityCorrelation]
    ) -> int | None:
        """Batch upsert using INSERT ... ON DUPLICATE KEY UPDATE.

        Requires unique index on (anchor_entity_id, entity_id).
        Processes in chunks of 500 to avoid MySQL packet limits.

        Args:
            correlations: List of ReliabilityCorrelation models to upsert

        Returns:
            Total number of affected rows on success, None on error
        """
        if not correlations:
            return 0

        total_affected = 0
        for i in range(0, len(correlations), BATCH_SIZE):
            chunk = correlations[i : i + BATCH_SIZE]
            affected = self._upsert_batch(chunk)
            if affected is None:
                logger.error("batch upsert failed", chunk_start_index=i)
                return None
            total_affected += affected

        logger.info(
            "upserted correlations",
            count=len(correlations),
            affected_rows=total_affected,
        )
        return total_affected

    def _upsert_batch(self, correlations: list[ReliabilityCorrelation]) -> int | None:
        """Upsert a single chunk using ON DUPLICATE KEY UPDATE.

        Args:
            correlations: List of ReliabilityCorrelation models (<= BATCH_SIZE)

        Returns:
            Number of affected rows on success, None on error
        """
        if not correlations:
            return 0

        record = (
            self._metrics.start_query("upsert", "reliability_correlations")
            if self._metrics
            else None
        )
        try:
            values_list = []
            for corr in correlations:
                values_list.append(
                    {
                        "anchor_entity_id": corr.anchor_entity_id,
                        "correlation_type": corr.correlation_type,
                        "entity_type": corr.entity_type,
                        "entity_id": corr.entity_id,
                        "services": _to_json(corr.services),
                        "start_time": parse_timestamp_to_utc(corr.start_time),
                        "end_time": parse_timestamp_to_utc(corr.end_time),
                        "reasoning": corr.reasoning,
                        "base_score": corr.base_score,
                        "final_score": corr.final_score,
                        "scoring_version": corr.scoring_version,
                    }
                )

            placeholders = ", ".join(
                [
                    f"(:anchor_entity_id_{idx}, :correlation_type_{idx}, "
                    f":entity_type_{idx}, :entity_id_{idx}, :services_{idx}, "
                    f":start_time_{idx}, :end_time_{idx}, "
                    f":reasoning_{idx}, "
                    f":base_score_{idx}, :final_score_{idx}, "
                    f":scoring_version_{idx})"
                    for idx in range(len(correlations))
                ]
            )

            sql = text(
                f"""
                INSERT INTO reliability_correlations
                    (anchor_entity_id, correlation_type, entity_type, entity_id,
                     services, start_time, end_time, reasoning,
                     base_score, final_score, scoring_version)
                VALUES {placeholders}
                ON DUPLICATE KEY UPDATE
                    correlation_type = VALUES(correlation_type),
                    services = VALUES(services),
                    start_time = VALUES(start_time),
                    end_time = VALUES(end_time),
                    reasoning = VALUES(reasoning),
                    base_score = VALUES(base_score),
                    final_score = VALUES(final_score),
                    scoring_version = VALUES(scoring_version)
                """
            )

            # Flatten parameters with unique keys
            params: dict[str, object] = {}
            for idx, values in enumerate(values_list):
                for key, value in values.items():
                    params[f"{key}_{idx}"] = value

            result = execute_with_retry(
                self._engine, sql, params, op="upsert:reliability_correlations"
            )

            if record:
                record(None)
            return result.rowcount
        except Exception as e:
            logger.exception("error in batch upsert", error=str(e))
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None
