"""Data Access Object for JIRA issues table."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Engine, func, insert, literal, or_, select, update

from common.daos.base_dao import BATCH_SIZE, BaseUpsertDAO, WriteOutcome
from common.metrics import DBMetrics
from common.models.jira_issue_record import JiraIssueRecord
from common.utils.log_utils import get_logger
from common.utils.retry_utils import execute_with_retry

logger = get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
jira_issues_table = JiraIssueRecord.__table__  # type: ignore[attr-defined]


@dataclass
class JiraHashInfo:
    """Contains hash and summary data for JIRA issue change detection.

    Used to cache LLM-generated summaries and detect content changes
    to avoid redundant FACADE/LLM API calls.
    """

    issue_key: str
    summary_hash: str | None
    comments_hash: str | None
    issue_summary: str | None
    issue_comments_summary: str | None


class JiraIssuesDAO(BaseUpsertDAO):
    """DAO for jira_issues table with batch operations."""

    TABLE_NAME = "jira_issues"

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
        from common.datasources.registry import get_source

        super().__init__(get_source("jira"), engine, metrics)

    def find_issue_by_key(self, issue_key: str) -> JiraIssueRecord | None:
        """
        Find a JIRA issue by its key (e.g., TCMR-123).

        Args:
            issue_key: JIRA issue key

        Returns:
            JiraIssueRecord if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", self.TABLE_NAME)
            if self._metrics
            else None
        )
        try:
            stmt = select(jira_issues_table).where(
                jira_issues_table.c.issue_key == issue_key
            )
            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if record:
                record(None)

            if result is None:
                logger.debug("no JIRA issue found", issue_key=issue_key)
                return None

            return JiraIssueRecord.model_validate(result._mapping)
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error finding JIRA issue",
                issue_key=issue_key,
                error=str(e),
            )
            return None

    def find_issues_by_keys(self, issue_keys: list[str]) -> list[JiraIssueRecord]:
        """
        Find JIRA issues by a list of issue keys.

        Args:
            issue_keys: List of JIRA issue keys (e.g., ["TCMR-123", "TCMR-456"])

        Returns:
            List of JiraIssueRecord models, empty list if none found or on error
        """
        if not issue_keys:
            return []

        record = (
            self._metrics.start_query("select", self.TABLE_NAME)
            if self._metrics
            else None
        )
        try:
            stmt = select(jira_issues_table).where(
                jira_issues_table.c.issue_key.in_(issue_keys),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)
            return [JiraIssueRecord.model_validate(row._mapping) for row in results]
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error finding JIRA issues by keys",
                count=len(issue_keys),
                error=str(e),
            )
            return []

    def get_issues_by_type(
        self, ticket_type: str, limit: int = 0
    ) -> list[JiraIssueRecord] | None:
        """
        Get JIRA issues by ticket type.

        Args:
            ticket_type: Ticket type (e.g., tcmr, operational)
            limit: Maximum number of records to return (0 for no limit)

        Returns:
            List of JiraIssueRecord on success, None on error
        """
        record = (
            self._metrics.start_query("select", self.TABLE_NAME)
            if self._metrics
            else None
        )
        try:
            stmt = (
                select(jira_issues_table)
                .where(jira_issues_table.c.ticket_type == ticket_type)
                .order_by(jira_issues_table.c.created_at.desc())
            )

            if limit > 0:
                stmt = stmt.limit(limit)

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)

            issues = []
            for row in results:
                issues.append(JiraIssueRecord.model_validate(row._mapping))

            logger.debug(
                "retrieved JIRA issues", ticket_type=ticket_type, count=len(issues)
            )
            return issues
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error getting JIRA issues by type",
                ticket_type=ticket_type,
                error=str(e),
            )
            return None

    ALLOWED_TIME_FIELDS: ClassVar[set[str]] = {"created_at", "tcmr_planned_start_date"}

    def find_issues_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        services: list[str] | None = None,
        time_field: str = "created_at",
    ) -> list[JiraIssueRecord] | None:
        """
        Find JIRA issues within a time range, optionally filtered by services.

        Args:
            start_time: Start of the time range (inclusive, UTC)
            end_time: End of the time range (inclusive, UTC)
            services: Optional service names to filter by (matches any within JSON array)
            time_field: Timestamp column to filter on (default: created_at)

        Returns:
            List of JiraIssueRecord on success, None on error

        Raises:
            ValueError: If time_field is not in the allowed set
        """
        if time_field not in self.ALLOWED_TIME_FIELDS:
            raise ValueError(
                f"Invalid time_field '{time_field}'. "
                f"Must be one of: {', '.join(sorted(self.ALLOWED_TIME_FIELDS))}"
            )

        col = getattr(jira_issues_table.c, time_field)

        record = (
            self._metrics.start_query("select", "jira_issues")
            if self._metrics
            else None
        )
        try:
            stmt = select(jira_issues_table).where(
                col >= start_time,
                col <= end_time,
            )

            if services:
                stmt = stmt.where(
                    or_(
                        *[
                            func.json_contains(
                                jira_issues_table.c.services,
                                literal(json.dumps(s)),
                            )
                            for s in services
                        ]
                    )
                )

            stmt = stmt.order_by(col.desc())

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)

            issues = [JiraIssueRecord.model_validate(row._mapping) for row in results]
            logger.debug(
                "retrieved JIRA issues by time range",
                start=start_time.isoformat(),
                end=end_time.isoformat(),
                services=services,
                count=len(issues),
            )
            return issues
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error finding JIRA issues by time range",
                start=start_time.isoformat(),
                end=end_time.isoformat(),
                services=services,
                error=str(e),
            )
            return None

    def get_issue_hashes_by_keys(
        self, issue_keys: list[str]
    ) -> dict[str, JiraHashInfo] | None:
        """
        Get issue hashes for change detection before calling LLM.

        Returns a dict keyed by issue_key for O(1) lookup.
        Processes in batches to avoid large IN clauses.

        Args:
            issue_keys: List of JIRA issue keys to fetch hashes for

        Returns:
            Dict mapping issue_key to JiraHashInfo on success, None on error
        """
        if not issue_keys:
            return {}

        hash_map: dict[str, JiraHashInfo] = {}
        record = None

        try:
            # Process in batches to avoid large IN clauses
            for i in range(0, len(issue_keys), BATCH_SIZE):
                chunk = issue_keys[i : i + BATCH_SIZE]

                stmt = select(
                    jira_issues_table.c.issue_key,
                    jira_issues_table.c.summary_hash,
                    jira_issues_table.c.comments_hash,
                    jira_issues_table.c.issue_summary,
                    jira_issues_table.c.issue_comments_summary,
                ).where(jira_issues_table.c.issue_key.in_(chunk))

                record = (
                    self._metrics.start_query("select", self.TABLE_NAME)
                    if self._metrics
                    else None
                )
                with self._engine.connect() as conn:
                    results = conn.execute(stmt).fetchall()
                if record:
                    record(None)

                for row in results:
                    hash_map[row.issue_key] = JiraHashInfo(
                        issue_key=row.issue_key,
                        summary_hash=row.summary_hash,
                        comments_hash=row.comments_hash,
                        issue_summary=row.issue_summary,
                        issue_comments_summary=row.issue_comments_summary,
                    )

            logger.debug(
                "retrieved issue hashes",
                hash_count=len(hash_map),
                key_count=len(issue_keys),
            )
            return hash_map
        except Exception as e:
            if record:
                record(e)
            logger.exception("error getting issue hashes", error=str(e))
            return None

    def insert_new_issue(self, issue: JiraIssueRecord) -> int | None:
        """
        Insert a new JIRA issue.

        Args:
            issue: JiraIssueRecord model to insert

        Returns:
            Auto-generated database ID on success, None on error
        """
        record = (
            self._metrics.start_query("insert", self.TABLE_NAME)
            if self._metrics
            else None
        )
        try:
            stmt = insert(jira_issues_table).values(
                issue_id=issue.issue_id,
                issue_key=issue.issue_key,
                ticket_type=issue.ticket_type,
                summary=issue.summary,
                status_name=issue.status_name,
                created_at=issue.created_at,
                issue_summary=issue.issue_summary,
                issue_comments_summary=issue.issue_comments_summary,
                summary_hash=issue.summary_hash,
                comments_hash=issue.comments_hash,
                tcmr_related_git_pr_link=issue.tcmr_related_git_pr_link,
                tcmr_related_services=issue.tcmr_related_services,
                services=issue.services,
                tcmr_planned_start_date=issue.tcmr_planned_start_date,
                tcmr_planned_end_date=issue.tcmr_planned_end_date,
            )

            result = execute_with_retry(self._engine, stmt, op="insert:jira_issues")

            if record:
                record(None)

            logger.info(
                "inserted JIRA issue",
                issue_key=issue.issue_key,
                id=result.lastrowid,
            )
            return result.lastrowid
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error inserting JIRA issue",
                issue_key=issue.issue_key,
                error=str(e),
            )
            return None

    def update_issue(self, issue: JiraIssueRecord) -> int | None:
        """
        Update an existing JIRA issue.

        Args:
            issue: JiraIssueRecord model with updated values

        Returns:
            Issue ID on success, None on error
        """
        record = (
            self._metrics.start_query("update", self.TABLE_NAME)
            if self._metrics
            else None
        )
        try:
            stmt = (
                update(jira_issues_table)
                .where(jira_issues_table.c.id == issue.id)
                .values(
                    issue_id=issue.issue_id,
                    ticket_type=issue.ticket_type,
                    summary=issue.summary,
                    status_name=issue.status_name,
                    issue_summary=issue.issue_summary,
                    issue_comments_summary=issue.issue_comments_summary,
                    summary_hash=issue.summary_hash,
                    comments_hash=issue.comments_hash,
                    tcmr_related_git_pr_link=issue.tcmr_related_git_pr_link,
                    tcmr_related_services=issue.tcmr_related_services,
                    services=issue.services,
                    tcmr_planned_start_date=issue.tcmr_planned_start_date,
                    tcmr_planned_end_date=issue.tcmr_planned_end_date,
                )
            )

            execute_with_retry(self._engine, stmt, op="update:jira_issues")

            if record:
                record(None)

            logger.info("updated JIRA issue", issue_key=issue.issue_key, id=issue.id)
            return issue.id
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error updating JIRA issue",
                issue_key=issue.issue_key,
                id=issue.id,
                error=str(e),
            )
            return None

    def insert_or_update_issue(self, issue: JiraIssueRecord) -> int | None:
        """
        Insert or update a JIRA issue (upsert).

        Checks if issue exists by issue_key. If not found, inserts.
        If found, updates.

        Args:
            issue: JiraIssueRecord model

        Returns:
            Issue ID on success, None on error
        """
        existing = self.find_issue_by_key(issue.issue_key)

        if existing is None:
            return self.insert_new_issue(issue)

        # Update existing issue with database ID
        issue.id = existing.id
        return self.update_issue(issue)

    def upsert_jira_issues_batch(
        self, issues: list[JiraIssueRecord], update_services: bool = True
    ) -> int | None:
        """
        Batch upsert using INSERT ... ON DUPLICATE KEY UPDATE.

        Requires unique index on issue_key.
        Processes in chunks of 500 to avoid MySQL packet limits.

        Args:
            issues: List of JiraIssueRecord models to upsert
            update_services: When False, the services column is excluded from the
                ON DUPLICATE KEY UPDATE clause so existing values are preserved.
                Pass False when service enrichment was skipped (e.g. mapping not loaded).

        Returns:
            Total number of affected rows on success, None on error
        """
        # Delegates to the spec-driven base ``upsert_batch``: rows are derived
        # generically and only the spec's ``update_columns`` are written on
        # conflict (never the four LLM-enriched columns). ``services`` is a mutable
        # base column, so it is in the update set by default; dropping it when
        # update_services=False preserves previously-resolved services.
        dropped = [] if update_services else ["services"]
        return self.upsert_batch(issues, dropped)

    def _upsert_batch(
        self, issues: list[JiraIssueRecord], update_services: bool = True
    ) -> int | None:
        """Upsert a single chunk (<= BATCH_SIZE) using ON DUPLICATE KEY UPDATE.

        Thin wrapper over the shared base implementation, retained for callers
        (and tests) that exercise a single pre-chunked batch directly.

        Args:
            issues: List of JiraIssueRecord models to upsert (<= BATCH_SIZE).
            update_services: When False, excludes services from the ON
                DUPLICATE KEY UPDATE clause so existing values are preserved.

        Returns:
            Number of affected rows on success, None on error.
        """
        update_columns = self._spec.update_columns
        if not update_services:
            update_columns = [c for c in update_columns if c != "services"]
        return self._upsert_chunk(issues, update_columns)

    def update_llm_fields(
        self,
        issue_key: str,
        issue_summary: str | None = None,
        issue_comments_summary: str | None = None,
        summary_hash: str | None = None,
        comments_hash: str | None = None,
        entered_at: datetime | None = None,
    ) -> WriteOutcome:
        """Update LLM-generated fields on an existing JIRA issue.

        Does NOT overwrite base fields (ticket_type, status_name, created_at, etc.).
        Identified by issue_key (unique in jira_issues via uq_jira_issue_key).

        Args:
            issue_key: JIRA issue key (e.g. OPS-123)
            issue_summary: LLM-generated issue summary
            issue_comments_summary: LLM-generated comments summary
            summary_hash: SHA256 of issue description
            comments_hash: SHA256 of aggregated comments
            entered_at: The enrichment write-group's staleness-guard timestamp
                (ADR 024), if this call has a message envelope to source one
                from. ``None`` (the default — no envelope available) makes
                the write unconditional regardless of the spec's staleness
                guard, same as before this parameter existed; callers that
                *do* have an ``entered_at`` should pass it so the guard
                applies here too.

        Returns:
            ``WriteOutcome.WRITTEN`` on success, ``WriteOutcome.BASE_NOT_FOUND``
            if no row matched, ``WriteOutcome.ERROR`` on database error.
        """
        # Delegates to the spec-driven base partial update. The jira spec does not
        # set ``enrichment_overwrites_with_none``, so None values are dropped —
        # a missing field never overwrites an existing one.
        return self.update_llm_fields_from_message(
            issue_key=issue_key,
            issue_summary=issue_summary,
            issue_comments_summary=issue_comments_summary,
            summary_hash=summary_hash,
            comments_hash=comments_hash,
            entered_at=entered_at,
        )
