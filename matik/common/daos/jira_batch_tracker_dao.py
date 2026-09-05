"""Data Access Object for JIRA batch tracker table."""

from sqlalchemy import Engine, select, update

from common.metrics import DBMetrics
from common.models.jira_batch_tracker import JiraBatchTracker
from common.utils.datetime_utils import utc_now_naive
from common.utils.log_utils import get_logger
from common.utils.retry_utils import execute_with_retry

logger = get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
jira_batch_tracker_table = JiraBatchTracker.__table__  # type: ignore[attr-defined]

TABLE_NAME = "jira_batch_tracker"


class JiraBatchTrackerDAO:
    """DAO for jira_batch_tracker table.

    Tracks batch processing status for JIRA ticket types.
    One row per ticket type (tcmr, operational, alert).
    """

    def __init__(
        self,
        engine: Engine,
        metrics: DBMetrics | None = None,
    ) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: DBMetrics for tracking query performance (optional)
        """
        self._engine = engine
        self._metrics = metrics

    def get_tracker_by_type(self, ticket_type: str) -> JiraBatchTracker | None:
        """
        Get the batch tracker for a specific ticket type.

        Args:
            ticket_type: Ticket type (tcmr, operational, alert)

        Returns:
            JiraBatchTracker if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", TABLE_NAME) if self._metrics else None
        )
        try:
            stmt = select(jira_batch_tracker_table).where(
                jira_batch_tracker_table.c.ticket_type == ticket_type
            )
            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug("no batch tracker found", ticket_type=ticket_type)
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return JiraBatchTracker.model_validate(result._mapping)
        except Exception as e:
            logger.exception(
                "error getting batch tracker",
                ticket_type=ticket_type,
                error=str(e),
            )
            if record:
                record(e)
            return None

    def update_tracker(self, tracker: JiraBatchTracker) -> bool:
        """
        Update the batch tracker for a specific ticket type.

        Args:
            tracker: JiraBatchTracker model with updated values

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("update", TABLE_NAME) if self._metrics else None
        )
        try:
            stmt = (
                update(jira_batch_tracker_table)
                .where(jira_batch_tracker_table.c.ticket_type == tracker.ticket_type)
                .values(
                    batch_start=tracker.batch_start,
                    batch_end=tracker.batch_end,
                    window_days=tracker.window_days,
                    status=tracker.status,
                    error_message=tracker.error_message,
                    last_processed_at=tracker.last_processed_at,
                    updated_at=utc_now_naive(),
                )
            )

            execute_with_retry(self._engine, stmt, op="update:jira_batch_tracker")

            if record:
                record(None)
            logger.info("updated batch tracker", ticket_type=tracker.ticket_type)
            return True
        except Exception as e:
            logger.exception(
                "error updating batch tracker",
                ticket_type=tracker.ticket_type,
                error=str(e),
            )
            if record:
                record(e)
            return False
