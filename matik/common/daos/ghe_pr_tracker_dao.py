"""Data Access Object for GHE PR tracker table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Engine, insert, select, update

from common.models.ghe_pr_tracker import GHEPRTracker
from common.utils import log_utils
from common.utils.datetime_utils import utc_now_naive
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
ghe_pr_tracker_table = GHEPRTracker.__table__  # type: ignore[attr-defined]


class GHEPRTrackerDAO:
    """DAO for ghe_pr_tracker table - tracks incremental crawling progress."""

    def __init__(self, engine: Engine, metrics: DBMetrics | None = None) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        self._engine = engine
        self._metrics = metrics

    def get_tracker_cutoff(self, org_id: int, repo_id: int) -> datetime | None:
        """
        Get cutoff date for incremental crawling.

        Args:
            org_id: GitHub organization ID
            repo_id: Repository ID

        Returns:
            Cutoff datetime if tracker exists, None if no tracker or error
        """
        record = (
            self._metrics.start_query("select", "ghe_pr_tracker")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pr_tracker_table.c.cutoff_date).where(
                ghe_pr_tracker_table.c.org_id == org_id,
                ghe_pr_tracker_table.c.repo_id == repo_id,
            )
            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug(f"No tracker found for org_id={org_id}, repo_id={repo_id}")
                if record:
                    record(None)
                return None

            # Return cutoff_date directly (column 0)
            cutoff_date: datetime = result[0]
            if record:
                record(None)
            return cutoff_date
        except Exception as e:
            logger.exception(
                "error getting tracker cutoff",
                org_id=org_id,
                repo_id=repo_id,
                error=str(e),
            )
            if record:
                record(e)
            return None

    def upsert_tracker(self, tracker: GHEPRTracker) -> bool:
        """
        Insert or update tracker record.

        Args:
            tracker: Tracker model with cutoff date and counts

        Returns:
            True on success, False on error
        """
        existing = self.get_tracker_cutoff(tracker.org_id, tracker.repo_id)

        if existing is None:
            return self._insert_tracker(tracker)
        return self._update_tracker(tracker)

    def _insert_tracker(self, tracker: GHEPRTracker) -> bool:
        """
        Insert new tracker record.

        Private helper method called by upsert_tracker().

        Args:
            tracker: Tracker model to insert

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("insert", "ghe_pr_tracker")
            if self._metrics
            else None
        )
        try:
            # Set timestamps to current UTC time if not provided
            now = utc_now_naive()
            created_at = tracker.created_at if tracker.created_at else now
            updated_at = tracker.updated_at if tracker.updated_at else now

            stmt = insert(ghe_pr_tracker_table).values(
                org_id=tracker.org_id,
                repo_id=tracker.repo_id,
                cutoff_date=tracker.cutoff_date,
                prs_crawled_count=tracker.prs_crawled_count,
                created_at=created_at,
                updated_at=updated_at,
            )

            execute_with_retry(self._engine, stmt, op="insert:ghe_pr_tracker")
            logger.info(
                f"Created tracker for org={tracker.org_id}, repo={tracker.repo_id}"
            )
            if record:
                record(None)
            return True
        except Exception as e:
            logger.exception(
                "error inserting tracker",
                org_id=tracker.org_id,
                repo_id=tracker.repo_id,
                error=str(e),
            )
            if record:
                record(e)
            return False

    def _update_tracker(self, tracker: GHEPRTracker) -> bool:
        """
        Update existing tracker record.

        Private helper method called by upsert_tracker().

        Args:
            tracker: Tracker model with updated values

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("update", "ghe_pr_tracker")
            if self._metrics
            else None
        )
        try:
            # Set updated_at to current UTC time
            updated_at = utc_now_naive()

            stmt = (
                update(ghe_pr_tracker_table)
                .where(
                    ghe_pr_tracker_table.c.org_id == tracker.org_id,
                    ghe_pr_tracker_table.c.repo_id == tracker.repo_id,
                )
                .values(
                    cutoff_date=tracker.cutoff_date,
                    prs_crawled_count=tracker.prs_crawled_count,
                    updated_at=updated_at,
                )
            )

            execute_with_retry(self._engine, stmt, op="update:ghe_pr_tracker")
            logger.info(
                f"Updated tracker for org={tracker.org_id}, repo={tracker.repo_id}"
            )
            if record:
                record(None)
            return True
        except Exception as e:
            logger.exception(
                "error updating tracker",
                org_id=tracker.org_id,
                repo_id=tracker.repo_id,
                error=str(e),
            )
            if record:
                record(e)
            return False

    def get_all_trackers(self) -> list[GHEPRTracker] | None:
        """
        Get all tracker records for monitoring.

        Returns:
            List of all tracker records on success, None on error
        """
        record = (
            self._metrics.start_query("select", "ghe_pr_tracker")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pr_tracker_table).order_by(
                ghe_pr_tracker_table.c.org_id.asc(),
                ghe_pr_tracker_table.c.repo_id.asc(),
            )
            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            trackers = []
            for row in results:
                tracker = GHEPRTracker.model_validate(row._mapping)
                trackers.append(tracker)

            logger.debug(f"Retrieved {len(trackers)} tracker records")
            if record:
                record(None)
            return trackers
        except Exception as e:
            logger.exception("error getting all trackers", error=str(e))
            if record:
                record(e)
            return None
