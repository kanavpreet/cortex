"""Data Access Object for GitHub Enterprise pull requests table.

``ghe_pull_requests`` is a single denormalized table: the globally-unique
GitHub ``pull_request_id`` is the primary key, and org/repo identity (org_id,
org_login, repo_id, repo_name) is stored inline. There are no separate
org/repo tables, so writes need no surrogate-id resolution and reads need no
joins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Engine, func, insert, literal, or_, select, text, update

from common.daos.base_dao import BATCH_SIZE, BaseUpsertDAO, WriteOutcome
from common.models.ghe_pr import GHEPullRequest
from common.utils import log_utils
from common.utils.datetime_utils import utc_now_naive
from common.utils.retry_utils import execute_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

logger = log_utils.get_logger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
ghe_pull_requests_table = GHEPullRequest.__table__  # type: ignore[attr-defined]


@dataclass
class GHEPullRequestWithRepo:
    """GHEPullRequest enriched with org and repo name for URL construction."""

    pr: GHEPullRequest
    org: str
    repo_name: str


@dataclass
class PRHashInfo:
    """Contains hash and summary for PR change detection."""

    pull_request_id: int
    repository_id: int
    description_hash: str | None
    pull_request_summary: str | None
    services: list[str] | None = None


class GHEPRDAO(BaseUpsertDAO):
    """DAO for ghe_pull_requests table with batch operations."""

    def __init__(self, engine: Engine, metrics: DBMetrics | None = None) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
            metrics: Optional DBMetrics for query instrumentation
        """
        from common.datasources.registry import get_source

        super().__init__(get_source("ghe_pr"), engine, metrics)

    def get_services_by_pr_identifiers(
        self,
        lookups: list[tuple[str, str, int]],
    ) -> dict[tuple[str, str, int], list[str]]:
        """
        Batch lookup of services for PRs identified by (org_login, repo_name, pr_number).

        Reads directly from ghe_pull_requests using the inline org/repo slug
        columns — no join needed.

        Args:
            lookups: List of (org_login, repo_name, pr_number) tuples

        Returns:
            Dict mapping each (org_login, repo_name, pr_number) to its services list.
            Only includes entries where services is not NULL.
            Returns empty dict on error or empty input.
        """
        if not lookups:
            return {}

        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            # Build parameterized tuple IN clause
            tuple_clauses = []
            params: dict[str, object] = {}
            for idx, (org, repo, pr_num) in enumerate(lookups):
                tuple_clauses.append(f"(:o_{idx}, :r_{idx}, :n_{idx})")
                params[f"o_{idx}"] = org
                params[f"r_{idx}"] = repo
                params[f"n_{idx}"] = pr_num

            in_clause = ", ".join(tuple_clauses)

            sql = text(f"""
                SELECT org_login, repo_name, pull_request_number, services
                FROM ghe_pull_requests
                WHERE (org_login, repo_name, pull_request_number) IN ({in_clause})
                  AND deleted_at IS NULL
                  AND services IS NOT NULL
            """)

            with self._engine.connect() as conn:
                results = conn.execute(sql, params).fetchall()

            result_map: dict[tuple[str, str, int], list[str]] = {}
            for row in results:
                key = (row.org_login, row.repo_name, row.pull_request_number)
                services = row.services
                # services may be a JSON string or already a list
                if isinstance(services, str):
                    services = json.loads(services)
                if isinstance(services, list):
                    result_map[key] = services

            logger.debug(
                f"Looked up services for {len(lookups)} PR identifiers, "
                f"found {len(result_map)} with services"
            )
            if record:
                record(None)
            return result_map
        except Exception as e:
            logger.exception("error looking up PR services", error=str(e))
            if record:
                record(e)
            return {}

    def get_pr_hashes_by_repo_id(
        self, org_id: int, repo_id: int
    ) -> dict[str, PRHashInfo] | None:
        """
        Get PR hashes for change detection before calling LLM.

        Returns dict keyed by pull_request_id (str) for O(1) lookup.
        Only returns non-deleted PRs for the given repo.

        Args:
            org_id: GitHub organization ID
            repo_id: GitHub repository ID

        Returns:
            Dict keyed by str(pull_request_id) on success, None on error
        """
        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = select(
                ghe_pull_requests_table.c.pull_request_id,
                ghe_pull_requests_table.c.repo_id,
                ghe_pull_requests_table.c.description_hash,
                ghe_pull_requests_table.c.pull_request_summary,
                ghe_pull_requests_table.c.services,
            ).where(
                ghe_pull_requests_table.c.org_id == org_id,
                ghe_pull_requests_table.c.repo_id == repo_id,
                ghe_pull_requests_table.c.deleted_at.is_(None),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            hash_map = {}
            for row in results:
                key = str(row.pull_request_id)
                hash_map[key] = PRHashInfo(
                    pull_request_id=row.pull_request_id,
                    repository_id=row.repo_id,
                    description_hash=row.description_hash,
                    pull_request_summary=row.pull_request_summary,
                    services=row.services,
                )

            logger.debug(f"Retrieved {len(hash_map)} PR hashes for repo {repo_id}")
            if record:
                record(None)
            return hash_map
        except Exception as e:
            logger.exception(
                "error getting PR hashes", org_id=org_id, repo_id=repo_id, error=str(e)
            )
            if record:
                record(e)
            return None

    def get_pr_hashes_by_ids(
        self,
        entity_ids: list[dict[str, int]],
    ) -> dict[str, PRHashInfo] | None:
        """
        Get PR description hashes for a list of specific PRs.

        Used by the enrichment API to look up existing hashes before deciding
        whether to call the LLM. ``pull_request_id`` is the primary key (and is
        globally unique), so the lookup keys solely on it. org_id/repository_id
        are read from each matched row to build the response key.

        Args:
            entity_ids: List of dicts that each contain at least a
                ``pull_request_id`` key (GitHub API PR ID). Extra keys such as
                org_id/repository_id are ignored.

        Returns:
            Dict keyed by "org_id:repo_id:pull_request_id" (GitHub API IDs) on
            success, None on database error. Missing entities are simply absent.
        """
        if not entity_ids:
            return {}

        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            hash_map: dict[str, PRHashInfo] = {}
            pr_ids = [eid["pull_request_id"] for eid in entity_ids]

            # Process in batches to avoid exceeding MySQL max_allowed_packet
            for i in range(0, len(pr_ids), BATCH_SIZE):
                chunk = pr_ids[i : i + BATCH_SIZE]

                stmt = select(
                    ghe_pull_requests_table.c.org_id,
                    ghe_pull_requests_table.c.repo_id,
                    ghe_pull_requests_table.c.pull_request_id,
                    ghe_pull_requests_table.c.description_hash,
                    ghe_pull_requests_table.c.pull_request_summary,
                    ghe_pull_requests_table.c.services,
                ).where(
                    ghe_pull_requests_table.c.pull_request_id.in_(chunk),
                    ghe_pull_requests_table.c.deleted_at.is_(None),
                )

                with self._engine.connect() as conn:
                    results = conn.execute(stmt).fetchall()

                for row in results:
                    # Key uses GitHub API IDs so callers can look up by entity_id fields
                    key = f"{row.org_id}:{row.repo_id}:{row.pull_request_id}"
                    hash_map[key] = PRHashInfo(
                        pull_request_id=row.pull_request_id,
                        repository_id=row.repo_id,
                        description_hash=row.description_hash,
                        pull_request_summary=row.pull_request_summary,
                        services=row.services,
                    )

            logger.debug(
                f"Retrieved hashes for {len(hash_map)} PRs out of {len(entity_ids)} requested"
            )
            if record:
                record(None)
            return hash_map
        except Exception as e:
            logger.exception(
                "error getting PR hashes by entity IDs",
                count=len(entity_ids),
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def find_ghe_pr(self, pull_request_id: int) -> GHEPullRequest | None:
        """
        Find a GHE pull request by its GitHub PR ID (primary key).

        Args:
            pull_request_id: GitHub pull request ID

        Returns:
            GHEPullRequest if found, None if not found or on error
        """
        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pull_requests_table).where(
                ghe_pull_requests_table.c.pull_request_id == pull_request_id,
            )

            with self._engine.connect() as conn:
                result = conn.execute(stmt).fetchone()

            if result is None:
                logger.debug(f"No PR found with pull_request_id={pull_request_id}")
                if record:
                    record(None)
                return None

            if record:
                record(None)
            return GHEPullRequest.model_validate(result._mapping)
        except Exception as e:
            logger.exception(
                "error finding PR",
                pull_request_id=pull_request_id,
                error=str(e),
            )
            if record:
                record(e)
            return None

    def find_prs_by_pull_request_ids(
        self, pull_request_ids: list[int]
    ) -> list[GHEPullRequest]:
        """
        Find GHE pull requests by a list of pull_request_ids.

        Args:
            pull_request_ids: List of GitHub PR IDs (e.g., [12345, 67890])

        Returns:
            List of GHEPullRequest models, empty list if none found or on error
        """
        if not pull_request_ids:
            return []

        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pull_requests_table).where(
                ghe_pull_requests_table.c.pull_request_id.in_(pull_request_ids),
                ghe_pull_requests_table.c.deleted_at.is_(None),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)
            return [GHEPullRequest.model_validate(row._mapping) for row in results]
        except Exception as e:
            logger.exception(
                "error finding PRs by pull_request_ids",
                count=len(pull_request_ids),
                error=str(e),
            )
            if record:
                record(e)
            return []

    def find_prs_with_repo_by_pull_request_ids(
        self, pull_request_ids: list[int]
    ) -> list[GHEPullRequestWithRepo]:
        """Find GHE pull requests by IDs, with their org and repo names.

        Used by MCP endpoints to supply the data needed to compute a direct
        URL to each PR (``{GHE_BASE_URL}/{org}/{repo_name}/pull/{pr_number}``).
        org/repo slugs are read inline from the PR row — no join needed.

        Args:
            pull_request_ids: List of GitHub PR IDs (e.g., [12345, 67890])

        Returns:
            List of GHEPullRequestWithRepo, empty list if none found or on error.
        """
        if not pull_request_ids:
            return []

        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pull_requests_table).where(
                ghe_pull_requests_table.c.pull_request_id.in_(pull_request_ids),
                ghe_pull_requests_table.c.deleted_at.is_(None),
            )

            with self._engine.connect() as conn:
                results = conn.execute(stmt).fetchall()

            if record:
                record(None)
            return [
                GHEPullRequestWithRepo(
                    pr=GHEPullRequest.model_validate(row._mapping),
                    org=row.org_login,
                    repo_name=row.repo_name,
                )
                for row in results
            ]
        except Exception as e:
            logger.exception(
                "error finding PRs with repo info by pull_request_ids",
                count=len(pull_request_ids),
                error=str(e),
            )
            if record:
                record(e)
            return []

    ALLOWED_TIME_FIELDS: ClassVar[set[str]] = {"created_at", "closed_at", "merged_at"}

    def find_prs_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        services: list[str] | None = None,
        time_field: str = "created_at",
    ) -> list[GHEPullRequest] | None:
        """
        Find GHE pull requests within a time range, optionally filtered by services.

        Args:
            start_time: Start of the time range (inclusive, UTC)
            end_time: End of the time range (inclusive, UTC)
            services: Optional service names to filter by (matches any within JSON array)
            time_field: Timestamp column to filter on (default: created_at)

        Returns:
            List of GHEPullRequest on success, None on error

        Raises:
            ValueError: If time_field is not in the allowed set
        """
        if time_field not in self.ALLOWED_TIME_FIELDS:
            raise ValueError(
                f"Invalid time_field '{time_field}'. "
                f"Must be one of: {', '.join(sorted(self.ALLOWED_TIME_FIELDS))}"
            )

        col = getattr(ghe_pull_requests_table.c, time_field)

        record = (
            self._metrics.start_query("select", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = select(ghe_pull_requests_table).where(
                col >= start_time,
                col <= end_time,
            )

            if services:
                stmt = stmt.where(
                    or_(
                        *[
                            func.json_contains(
                                ghe_pull_requests_table.c.services,
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

            prs = [GHEPullRequest.model_validate(row._mapping) for row in results]
            logger.debug(
                f"retrieved GHE prs by time range start={start_time.isoformat()} "
                f"end={end_time.isoformat()} services={services} count={len(prs)}"
            )
            return prs
        except Exception as e:
            if record:
                record(e)
            logger.exception(
                "error finding GHE prs by time range",
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
                services=services,
                error=str(e),
            )
            return None

    def insert_or_update_ghe_pr(self, pr: GHEPullRequest) -> int | None:
        """
        Insert or update a GHE pull request (upsert), keyed on pull_request_id.

        Args:
            pr: GHEPullRequest model

        Returns:
            pull_request_id on success, None on error
        """
        existing = self.find_ghe_pr(pr.pull_request_id)

        if existing is None:
            return self.insert_new_pr(pr)

        return self.update_pr(pr)

    def insert_new_pr(self, pr: GHEPullRequest) -> int | None:
        """
        Insert a new GHE pull request.

        Args:
            pr: GHEPullRequest model to insert

        Returns:
            pull_request_id on success, None on error
        """
        record = (
            self._metrics.start_query("insert", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = insert(ghe_pull_requests_table).values(
                pull_request_id=pr.pull_request_id,
                pull_request_number=pr.pull_request_number,
                org_id=pr.org_id,
                org_login=pr.org_login,
                repo_id=pr.repo_id,
                repo_name=pr.repo_name,
                merged=pr.merged,
                state=pr.state,
                locked=pr.locked,
                created_at=pr.created_at,
                closed_at=pr.closed_at,
                merged_at=pr.merged_at,
                last_updated_at=pr.last_updated_at,
                target_branch_name=pr.target_branch_name,
                pull_request_summary=pr.pull_request_summary,
                jira_tcmr_key=pr.jira_tcmr_key,
                environment=pr.environment,
                description_hash=pr.description_hash,
                services=pr.services,
            )

            execute_with_retry(self._engine, stmt, op="insert:ghe_pull_requests")

            logger.info(f"Inserted GHE PR (pull_request_id={pr.pull_request_id})")
            if record:
                record(None)
            return pr.pull_request_id
        except Exception as e:
            logger.exception(
                "error inserting GHE PR",
                pull_request_id=pr.pull_request_id,
                error=str(e),
            )
            if record:
                record(e)
            return None

    def update_pr(self, pr: GHEPullRequest) -> int | None:
        """
        Update an existing GHE pull request, keyed on pull_request_id.

        Refreshes the org/repo slug columns (handles renames) and resets
        deleted_at to NULL when re-discovering a previously deleted PR.

        Args:
            pr: GHEPullRequest model with updated values

        Returns:
            pull_request_id on success, None on error
        """
        record = (
            self._metrics.start_query("update", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = (
                update(ghe_pull_requests_table)
                .where(
                    ghe_pull_requests_table.c.pull_request_id == pr.pull_request_id,
                )
                .values(
                    pull_request_number=pr.pull_request_number,
                    org_login=pr.org_login,
                    repo_name=pr.repo_name,
                    merged=pr.merged,
                    state=pr.state,
                    locked=pr.locked,
                    closed_at=pr.closed_at,
                    merged_at=pr.merged_at,
                    last_updated_at=pr.last_updated_at,
                    target_branch_name=pr.target_branch_name,
                    pull_request_summary=pr.pull_request_summary,
                    jira_tcmr_key=pr.jira_tcmr_key,
                    environment=pr.environment,
                    description_hash=pr.description_hash,
                    services=pr.services,
                    deleted_at=None,  # Reset soft delete on update
                )
            )

            execute_with_retry(self._engine, stmt, op="update:ghe_pull_requests")

            logger.info(f"Updated GHE PR (pull_request_id={pr.pull_request_id})")
            if record:
                record(None)
            return pr.pull_request_id
        except Exception as e:
            logger.exception(
                "error updating GHE PR",
                pull_request_id=pr.pull_request_id,
                error=str(e),
            )
            if record:
                record(e)
            return None

    def delete_ghe_pr(self, pull_request_id: int) -> bool:
        """
        Soft delete a GHE pull request by setting deleted_at timestamp.

        Args:
            pull_request_id: GitHub pull request ID

        Returns:
            True on success, False on error
        """
        record = (
            self._metrics.start_query("update", "ghe_pull_requests")
            if self._metrics
            else None
        )
        try:
            stmt = (
                update(ghe_pull_requests_table)
                .where(ghe_pull_requests_table.c.pull_request_id == pull_request_id)
                .values(deleted_at=utc_now_naive())
            )

            execute_with_retry(self._engine, stmt, op="delete:ghe_pull_requests")

            logger.info(f"Soft deleted GHE PR with pull_request_id={pull_request_id}")
            if record:
                record(None)
            return True
        except Exception as e:
            logger.exception(
                "error soft deleting GHE PR",
                pull_request_id=pull_request_id,
                error=str(e),
            )
            if record:
                record(e)
            return False

    def upsert_ghe_prs_batch(
        self,
        prs: list[GHEPullRequest],
    ) -> int | None:
        """
        Batch upsert using INSERT ... ON DUPLICATE KEY UPDATE.

        Keyed on the primary key ``pull_request_id``. Each PR carries org/repo
        identity inline, so no surrogate-id resolution is needed. Delegates to the
        spec-driven base ``upsert_batch``: rows are derived generically from the
        model and only the spec's ``update_columns`` are written on conflict — it
        never overwrites the two LLM-enriched columns, and ``deleted_at`` (in the
        update set) is reset to NULL to revive a re-discovered soft-deleted PR.

        Args:
            prs: List of GHEPullRequest models to upsert

        Returns:
            Total number of affected rows on success, None on error.
        """
        return self.upsert_batch(prs)

    def _upsert_batch(self, prs: list[GHEPullRequest]) -> int | None:
        """Upsert a single chunk (<= BATCH_SIZE) using ON DUPLICATE KEY UPDATE.

        Thin wrapper over the shared base implementation, retained for callers
        (and tests) that exercise a single pre-chunked batch directly.

        Args:
            prs: List of GHEPullRequest models to upsert (<= BATCH_SIZE).

        Returns:
            Number of affected rows on success, None on error.
        """
        return self._upsert_chunk(prs, self._spec.update_columns)

    def update_llm_fields(
        self,
        pull_request_id: int,
        pull_request_summary: str | None,
        description_hash: str | None,
        entered_at: datetime | None = None,
    ) -> WriteOutcome:
        """Update LLM-generated fields on an existing GHE pull request.

        Does NOT overwrite base fields (state, merged, timestamps, etc.).
        Identified solely by the primary key ``pull_request_id``. Delegates to the
        spec-driven base partial update; the ghe_pr spec sets
        ``enrichment_overwrites_with_none`` so both LLM columns are written even
        when None (blanks the column) — preserving the historical "always write
        both" semantics. The Scribe caller always supplies real values from the
        enrichment message.

        Args:
            pull_request_id: GitHub API internal PR ID (primary key)
            pull_request_summary: LLM-generated PR description summary
            description_hash: SHA256 of PR description
            entered_at: The enrichment write-group's staleness-guard timestamp
                (ADR 024), if this call has a message envelope to source one
                from. ``None`` (the default — no envelope available) makes
                the write unconditional regardless of the spec's staleness
                guard, same as before this parameter existed; callers that
                *do* have an ``entered_at`` should pass it so the guard
                applies here too.

        Returns:
            ``WriteOutcome.WRITTEN`` on success, ``WriteOutcome.BASE_NOT_FOUND``
            when the base record does not exist, ``WriteOutcome.ERROR`` on
            database error.
        """
        return self.update_llm_fields_from_message(
            pull_request_id=pull_request_id,
            pull_request_summary=pull_request_summary,
            description_hash=description_hash,
            entered_at=entered_at,
        )
