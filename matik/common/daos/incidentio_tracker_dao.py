"""Data Access Object for Incident.io tracker table."""

from typing import TYPE_CHECKING

from sqlalchemy import Engine, select, update

from common.models.incidentio_tracker import IncidentIOTracker
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
incidentio_tracker_table = IncidentIOTracker.__table__  # type: ignore[attr-defined]


class IncidentIOTrackerDAO:
    """DAO for incidentio_tracker table - tracks last recorded incident.

    This table uses a single-row pattern where id=1 is always the only row.
    The row is initialized via migration 000007_initialize_incidentio_tracker_table.up.sql.
    """

    def __init__(self, engine: Engine, metrics: "DBMetrics | None" = None) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        self._engine = engine
        self._metrics = metrics

    def find_last_recorded(self) -> IncidentIOTracker | None:
        """
        Get the single tracker record (id=1).

        Returns:
            IncidentIOTracker if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", "incidentio_tracker")
            if self._metrics
            else None
        )
        try:
            stmt = select(incidentio_tracker_table).where(
                incidentio_tracker_table.c.id == 1,
            )

            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug("no tracker record found", id=1)
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return IncidentIOTracker.model_validate(result._mapping)
        except Exception as e:
            logger.exception("error finding tracker record", error=str(e))
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def update_tracker(self, tracker: IncidentIOTracker) -> bool:
        """
        Update the tracker record (id=1).

        Args:
            tracker: IncidentIOTracker model with updated values

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("update", "incidentio_tracker")
            if self._metrics
            else None
        )
        try:
            # Normalize timestamp to UTC (defensive, matches Go implementation)
            stmt = (
                update(incidentio_tracker_table)
                .where(incidentio_tracker_table.c.id == 1)
                .values(
                    timestamp=parse_timestamp_to_utc(tracker.timestamp),
                    status=tracker.status,
                    error_message=tracker.error_message,
                    initial_sync_complete=tracker.initial_sync_complete,
                    last_updated_at_cursor=parse_timestamp_to_utc(
                        tracker.last_updated_at_cursor
                    ),
                )
            )

            execute_with_retry(self._engine, stmt, op="update:incidentio_tracker")

            logger.info(
                "updated tracker",
                initial_sync_complete=tracker.initial_sync_complete,
                status=tracker.status,
            )
            if record:
                record(None)
            return True
        except Exception as e:
            logger.exception("error updating tracker", error=str(e))
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return False
