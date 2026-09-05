"""Data Access Object for reliability_correlation_groups table."""

from typing import TYPE_CHECKING

from sqlalchemy import Engine, insert, select, update

from common.models.reliability_correlation_group import ReliabilityCorrelationGroup
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics


logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
reliability_correlation_groups_table = (
    ReliabilityCorrelationGroup.__table__  # type: ignore[attr-defined]
)


class ReliabilityCorrelationGroupDAO:
    """DAO for reliability_correlation_groups table."""

    def __init__(self, engine: Engine, metrics: "DBMetrics | None" = None) -> None:
        """Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        self._engine = engine
        self._metrics = metrics

    def find_group(self, anchor_entity_id: str) -> ReliabilityCorrelationGroup | None:
        """Find a correlation group by anchor_entity_id.

        Args:
            anchor_entity_id: Business key of the anchor entity (e.g., INC-99)

        Returns:
            ReliabilityCorrelationGroup if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", "reliability_correlation_groups")
            if self._metrics
            else None
        )
        try:
            stmt = select(reliability_correlation_groups_table).where(
                reliability_correlation_groups_table.c.anchor_entity_id
                == anchor_entity_id,
            )

            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug(
                    "no correlation group found",
                    anchor_entity_id=anchor_entity_id,
                )
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return ReliabilityCorrelationGroup.model_validate(result._mapping)
        except Exception as e:
            logger.exception(
                "error finding correlation group",
                anchor_entity_id=anchor_entity_id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def insert_or_update_group(self, group: ReliabilityCorrelationGroup) -> int | None:
        """Insert or update a correlation group (upsert by anchor_entity_id).

        Args:
            group: ReliabilityCorrelationGroup model

        Returns:
            Database ID on success, None on error
        """
        existing = self.find_group(group.anchor_entity_id)

        if existing is None:
            return self._insert_group(group)

        group.id = existing.id
        return self._update_group(group)

    def _insert_group(self, group: ReliabilityCorrelationGroup) -> int | None:
        """Insert a new correlation group.

        Args:
            group: ReliabilityCorrelationGroup model to insert

        Returns:
            Auto-generated database ID on success, None on error
        """
        record = (
            self._metrics.start_query("insert", "reliability_correlation_groups")
            if self._metrics
            else None
        )
        try:
            stmt = insert(reliability_correlation_groups_table).values(
                anchor_entity_id=group.anchor_entity_id,
                anchor_type=group.anchor_type,
                services=group.services,
                start_time=parse_timestamp_to_utc(group.start_time),
                end_time=parse_timestamp_to_utc(group.end_time),
                correlation_timestamp=parse_timestamp_to_utc(
                    group.correlation_timestamp
                ),
                base_score=group.base_score,
                final_score=group.final_score,
                scoring_version=group.scoring_version,
                feedback_list=group.feedback_list,
                feedback_state=group.feedback_state,
                review_status=group.review_status,
                last_reviewed_at=parse_timestamp_to_utc(group.last_reviewed_at),
            )

            result = execute_with_retry(
                self._engine, stmt, op="insert:reliability_correlation_groups"
            )

            logger.info(
                "inserted correlation group",
                anchor_entity_id=group.anchor_entity_id,
                id=result.lastrowid,
            )
            if record:
                record(None)
            return result.lastrowid
        except Exception as e:
            logger.exception(
                "error inserting correlation group",
                anchor_entity_id=group.anchor_entity_id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def _update_group(self, group: ReliabilityCorrelationGroup) -> int | None:
        """Update an existing correlation group.

        Args:
            group: ReliabilityCorrelationGroup model with updated values (must have id set)

        Returns:
            Database ID on success, None on error
        """
        record = (
            self._metrics.start_query("update", "reliability_correlation_groups")
            if self._metrics
            else None
        )
        try:
            stmt = (
                update(reliability_correlation_groups_table)
                .where(reliability_correlation_groups_table.c.id == group.id)
                .values(
                    anchor_type=group.anchor_type,
                    services=group.services,
                    start_time=parse_timestamp_to_utc(group.start_time),
                    end_time=parse_timestamp_to_utc(group.end_time),
                    correlation_timestamp=parse_timestamp_to_utc(
                        group.correlation_timestamp
                    ),
                    base_score=group.base_score,
                    final_score=group.final_score,
                    scoring_version=group.scoring_version,
                    feedback_list=group.feedback_list,
                    feedback_state=group.feedback_state,
                    review_status=group.review_status,
                    last_reviewed_at=parse_timestamp_to_utc(group.last_reviewed_at),
                )
            )

            execute_with_retry(
                self._engine, stmt, op="update:reliability_correlation_groups"
            )

            logger.info(
                "updated correlation group",
                anchor_entity_id=group.anchor_entity_id,
                id=group.id,
            )
            if record:
                record(None)
            return group.id
        except Exception as e:
            logger.exception(
                "error updating correlation group",
                anchor_entity_id=group.anchor_entity_id,
                id=group.id,
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None
