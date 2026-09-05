"""Data Access Object for GHE org crawl tracker table."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Engine, select, text

from common.models.ghe_org_crawl_tracker import GHEOrgCrawlTracker
from common.utils import log_utils
from common.utils.datetime_utils import utc_now_naive
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
ghe_org_crawl_tracker_table = GHEOrgCrawlTracker.__table__  # type: ignore[attr-defined]


class GHEOrgCrawlTrackerDAO:
    """DAO for ghe_org_crawl_tracker - tracks last successful crawl per org."""

    def __init__(self, engine: Engine, metrics: DBMetrics | None = None) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        self._engine = engine
        self._metrics = metrics

    def get_last_crawled_at(self, org_id: int) -> datetime | None:
        """
        Get the last successful crawl start time for an organization.

        Args:
            org_id: GitHub organization ID

        Returns:
            last_crawled_at datetime if a record exists, None if none or error
        """
        record = (
            self._metrics.start_query("select", "ghe_org_crawl_tracker")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_org_crawl_tracker_table.c.last_crawled_at).where(
                ghe_org_crawl_tracker_table.c.org_id == org_id,
            )
            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug(f"No org crawl tracker found for org_id={org_id}")
                if record:
                    record(None)
                return None

            last_crawled_at: datetime = result[0]
            if record:
                record(None)
            return last_crawled_at
        except Exception as e:
            logger.exception(
                "error getting org crawl tracker", org_id=org_id, error=str(e)
            )
            if record:
                record(e)
            return None

    def upsert_watermark(self, tracker: GHEOrgCrawlTracker) -> bool:
        """
        Atomically insert or update the per-org crawl watermark.

        Uses INSERT ... ON DUPLICATE KEY UPDATE (keyed on the unique org_id) so
        concurrent writers cannot race between an existence check and an insert
        and trip the unique constraint. On conflict only last_crawled_at and
        updated_at are refreshed; created_at is preserved.

        Args:
            tracker: Tracker model with org_id and last_crawled_at

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("upsert", "ghe_org_crawl_tracker")
            if self._metrics
            else None
        )
        try:
            now = utc_now_naive()
            created_at = tracker.created_at if tracker.created_at else now
            updated_at = tracker.updated_at if tracker.updated_at else now

            # SQLAlchemy core doesn't directly support ON DUPLICATE KEY UPDATE,
            # so use raw SQL (matching GHEPRDAO's upsert pattern).
            sql = text(
                """
                INSERT INTO ghe_org_crawl_tracker
                    (org_id, last_crawled_at, created_at, updated_at)
                VALUES (:org_id, :last_crawled_at, :created_at, :updated_at)
                ON DUPLICATE KEY UPDATE
                    last_crawled_at = VALUES(last_crawled_at),
                    updated_at = VALUES(updated_at)
                """
            )

            execute_with_retry(
                self._engine,
                sql,
                {
                    "org_id": tracker.org_id,
                    "last_crawled_at": tracker.last_crawled_at,
                    "created_at": created_at,
                    "updated_at": updated_at,
                },
                op="upsert:ghe_org_crawl_tracker",
            )
            logger.info(f"Upserted org crawl tracker for org={tracker.org_id}")
            if record:
                record(None)
            return True
        except Exception as e:
            logger.exception(
                "error upserting org crawl tracker",
                org_id=tracker.org_id,
                error=str(e),
            )
            if record:
                record(e)
            return False
