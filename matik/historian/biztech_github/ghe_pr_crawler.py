"""GHE PR Crawler - crawls GitHub Enterprise for pull requests."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import yaml
from dateutil.relativedelta import relativedelta

from common.clients.artifactory_client import ArtifactoryClient
from common.clients.ghe_client import (
    GHEClient,
    GHEOrg,
    GHERepo,
    PRHashCache,
)
from common.clients.matik_api_client import MatikApiClient
from common.clients.sqs_publisher import SQSPublisher
from common.constants import ARTIFACTORY_SUMMARY_FILE_PATH, TASK_ID_HEADER
from common.metrics.ghe_cache_metrics import GHECacheMetrics
from common.metrics.job_metrics import JobMetrics
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.ghe_org_crawl_tracker import GHEOrgCrawlTracker
from common.models.ghe_pr import GHEPullRequest
from common.models.ghe_pr_tracker import GHEPRTracker
from common.models.scribe_messages import GHEPRBaseMessage, GHEPRTrackerBaseMessage
from common.utils import log_utils
from common.utils.datetime_utils import utc_now_naive
from historian.base.crawler import CrawlerResult

logger = log_utils.get_logger(__name__)

# Default values
DEFAULT_PAGE_SIZE = 100
DEFAULT_TRACKER_LOOKBACK_DAYS = 30
DEFAULT_CUTOFF_DATE = datetime(2026, 1, 1, 0, 0, 0)  # noqa: DTZ001
DEFAULT_MAX_CONCURRENT_REPOS = 5
# Minutes subtracted from the per-org watermark to absorb clock skew between
# the crawler host and GitHub when deciding which repos to re-scan.
DEFAULT_REPO_ACTIVITY_BUFFER_MINUTES = 15


@dataclass
class CrawlerMetrics:
    """Simple metrics tracking for the GHE PR crawler."""

    # Counts
    organizations_processed: int = 0
    repositories_processed: int = 0
    prs_crawled: int = 0
    prs_upserted: int = 0

    # API call counts
    api_calls_ghe: int = 0
    api_calls_matik: int = 0
    sqs_messages_sent: int = 0

    # Timing (seconds)
    total_run_duration: float = 0.0
    org_durations: list[float] = field(default_factory=list)
    repo_durations: list[float] = field(default_factory=list)

    # Errors
    org_errors: int = 0
    repo_errors: int = 0

    def to_dict(self) -> dict[str, int | float]:
        """Convert to dict for logging."""
        return {
            "organizations_processed": self.organizations_processed,
            "repositories_processed": self.repositories_processed,
            "prs_crawled": self.prs_crawled,
            "prs_upserted": self.prs_upserted,
            "api_calls_ghe": self.api_calls_ghe,
            "api_calls_matik": self.api_calls_matik,
            "sqs_messages_sent": self.sqs_messages_sent,
            "total_api_calls": self.api_calls_ghe + self.api_calls_matik,
            "total_run_duration_sec": round(self.total_run_duration, 2),
            "avg_org_duration_sec": (
                round(sum(self.org_durations) / len(self.org_durations), 2)
                if self.org_durations
                else 0
            ),
            "avg_repo_duration_sec": (
                round(sum(self.repo_durations) / len(self.repo_durations), 2)
                if self.repo_durations
                else 0
            ),
            "org_errors": self.org_errors,
            "repo_errors": self.repo_errors,
        }


@dataclass
class GHEPRCrawler:
    """Crawls GitHub Enterprise for pull requests and publishes them to SQS.

    PR writes and tracker updates are sent to the Scribe SQS queue.
    Organization/repository writes and read operations (tracker cutoff,
    hash cache) still go through the Matik API.
    """

    ghe_client: GHEClient
    matik_client: MatikApiClient
    sqs_publisher: SQSPublisher
    config: BiztechGitHubConfig
    metrics: CrawlerMetrics = field(default_factory=CrawlerMetrics)
    job_metrics: JobMetrics | None = None
    """Optional Telescope metrics for job instrumentation."""
    cache_metrics: GHECacheMetrics | None = None
    """Optional Telescope metrics for cache and crawler statistics."""
    artifactory_client: ArtifactoryClient | None = None

    async def run(self) -> int:
        """Main entry point - processes all configured organizations.

        Generates a request ID for the entire crawler run for distributed tracing.

        Returns:
            Total number of PRs crawled across all organizations.
        """
        # Generate request ID for entire crawler run
        log_utils.generate_task_id(__name__)
        logger.info("Starting GHE PR crawler run")
        start_time = time.perf_counter()

        # Start job metrics tracking if available
        record_job = None
        if self.job_metrics:
            record_job = self.job_metrics.start_job("ghe_pr")

        # Fetch summary data from Artifactory and build service index
        service_index: dict[str, list[tuple[str, str]]] = {}
        if self.artifactory_client:
            try:
                summary_file = self.artifactory_client.get_file(
                    ARTIFACTORY_SUMMARY_FILE_PATH
                )
                summary_data = yaml.safe_load(summary_file)
                if summary_data:
                    service_index = self._build_service_index(summary_data)
            except Exception:
                logger.exception("Failed to fetch Artifactory summary file")

        try:
            total_prs = 0
            for org_name in self.config.organizations:
                logger.info("Processing organization", org=org_name)
                org_start_time = time.perf_counter()
                try:
                    prs = await self.process_organization(org_name, service_index)
                    total_prs += prs
                    self.metrics.organizations_processed += 1
                    logger.info(
                        "Completed: %d PRs crawled",
                        prs,
                        org=org_name,
                    )
                except Exception:
                    self.metrics.org_errors += 1
                    # Record non-fatal error in job metrics
                    if self.job_metrics:
                        self.job_metrics.record_job_error("ghe_pr", "org_processing")
                    logger.exception("Failed to process organization", org=org_name)
                finally:
                    self.metrics.org_durations.append(
                        time.perf_counter() - org_start_time
                    )

            # Record successful job completion
            if record_job:
                record_job(total_prs, None)

            return total_prs
        except Exception as e:
            # Record job failure
            if record_job:
                record_job(0, e)
            raise
        finally:
            self.metrics.total_run_duration = time.perf_counter() - start_time
            logger.info("Crawler metrics", **self.metrics.to_dict())

            # Export crawler statistics to Telescope
            if self.cache_metrics:
                self.cache_metrics.record_crawler_stats(
                    orgs_processed=self.metrics.organizations_processed,
                    repos_processed=self.metrics.repositories_processed,
                    prs_upserted=self.metrics.prs_upserted,
                    api_calls_ghe=self.metrics.api_calls_ghe,
                    api_calls_matik=self.metrics.api_calls_matik,
                )

            log_utils.clear_task_id()

    def dispatch(self) -> CrawlerResult:
        """Run the crawl and return a CrawlerResult (shared historian contract).

        GHE's crawl is a fan-out over orgs/repos rather than the producer/consumer
        shape of ``BaseCrawler``, so this does not subclass ``BaseCrawler``; it just
        conforms to the same ``dispatch() -> CrawlerResult`` contract the shared
        ``run_historian`` shell expects. Owns the event loop (the crawl is async)
        and closes the GHE client on the way out. ``run()`` records job metrics
        internally, so the ``build_and_run`` callback does not start a job for GHE.
        """

        async def _run() -> CrawlerResult:
            try:
                total_prs = await self.run()
                return CrawlerResult(
                    records_processed=total_prs,
                    success=True,
                )
            except Exception as e:
                logger.exception("GHE PR crawler failed")
                return CrawlerResult(
                    records_processed=0,
                    success=False,
                    error_message=str(e),
                )
            finally:
                await self.ghe_client.close()
                logger.info("GHE client closed")

        return asyncio.run(_run())

    async def process_organization(
        self,
        org_name: str,
        service_index: dict[str, list[tuple[str, str]]] | None = None,
    ) -> int:
        """Process a single organization - fetch org, repos, and PRs.

        Args:
            org_name: GitHub organization login name.
            service_index: Pre-built map of repo_url to service entries.

        Returns:
            Number of PRs crawled for this organization.
        """
        # Record the run start up front; on a clean run this becomes the new
        # per-org watermark so the next run only re-scans repos pushed since now.
        run_started_at = utc_now_naive()

        # 1. Fetch org from GitHub API
        org = await self.ghe_client.get_organization(org_name)
        self.metrics.api_calls_ghe += 1
        logger.info("Fetched organization: (org_id=%d)", org.org_id, org=org_name)

        # 2. Fetch repos from GitHub API, skipping repos with no activity since
        # the last successful crawl (minus a skew buffer) and archived repos.
        pushed_since = await self._get_repos_pushed_since(org.org_id)
        repos = await self.ghe_client.get_repos(org, pushed_since=pushed_since)
        self.metrics.api_calls_ghe += 1
        logger.info("Found %d active repositories", len(repos), org=org_name)

        # 3. Process repos concurrently with bounded concurrency
        max_concurrent = self._get_max_concurrent_repos()
        repo_semaphore = asyncio.Semaphore(max_concurrent)

        # Track repo-level failures so we only advance the watermark on a clean
        # run; otherwise a repo that errored would be skipped until its next push.
        org_had_repo_error = False

        async def process_repo_bounded(repo: GHERepo) -> int:
            nonlocal org_had_repo_error
            async with repo_semaphore:
                repo_start_time = time.perf_counter()
                try:
                    return await self.process_repository(org, repo, service_index)
                except Exception:
                    org_had_repo_error = True
                    self.metrics.repo_errors += 1
                    if self.job_metrics:
                        self.job_metrics.record_job_error("ghe_pr", "repo_processing")
                    logger.exception(
                        "Failed to process repository",
                        repo=repo.repo_name,
                        org=org_name,
                    )
                    return 0
                finally:
                    self.metrics.repo_durations.append(
                        time.perf_counter() - repo_start_time
                    )

        # Process all repos concurrently (bounded by semaphore)
        results = await asyncio.gather(
            *[process_repo_bounded(repo) for repo in repos],
            return_exceptions=True,
        )

        # Sum successful results, log exceptions
        total_prs = 0
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                org_had_repo_error = True
                self.metrics.repo_errors += 1
                if self.job_metrics:
                    self.job_metrics.record_job_error("ghe_pr", "repo_exception")
                logger.error(
                    "Repository processing raised exception",
                    repo=repos[i].repo_name,
                    org=org_name,
                    error=str(result),
                )
            elif isinstance(result, int):
                total_prs += result

        # 4. Advance the per-org watermark only when every repo succeeded.
        if org_had_repo_error:
            logger.warning(
                "Not advancing crawl watermark due to repo errors", org=org_name
            )
        else:
            await self._set_org_watermark(org.org_id, run_started_at)

        return total_prs

    async def process_repository(
        self,
        org: GHEOrg,
        repo: GHERepo,
        service_index: dict[str, list[tuple[str, str]]] | None = None,
    ) -> int:
        """Process a single repository - fetch and store PRs.

        Args:
            org: GHEOrg identity.
            repo: GHERepo identity.
            service_index: Pre-built map of repo_url to service entries.

        Returns:
            Number of PRs crawled for this repository.
        """
        # 1. Get tracker cutoff date from API (keyed on GitHub org/repo IDs)
        cutoff = await self._get_tracker_cutoff(org.org_id, repo.repo_id)
        tracker_config = "tracker cutoff" if cutoff else "configured cutoff"
        if cutoff is None:
            cutoff = self._get_configured_cutoff()
        logger.info(
            "Using %s date: %s",
            tracker_config,
            cutoff,
            org=org.org_login,
            repo=repo.repo_name,
        )

        # 2. Fetch PRs from GitHub and publish each to the Enricher. The Enricher
        # owns LLM summarization and hash-based change detection, so no hash cache
        # is fetched here (an empty cache is passed for file/service-fetch gating).
        page_size = self._get_page_size()
        pr_results = await self.ghe_client.list_pull_requests_with_hash_cache(
            org, repo, page_size, cutoff, PRHashCache()
        )
        self.metrics.api_calls_ghe += 1

        # Separate PR models and their changed file paths
        files = {r.pr.pull_request_id: r.files for r in pr_results if r.files}
        prs = [r.pr for r in pr_results]

        self.metrics.prs_crawled += len(prs)
        logger.info("Fetched %d PRs", len(prs), org=org.org_login, repo=repo.repo_name)

        # 4. Tag PRs with services based on files changed
        if service_index:
            repo_url = f"{self.config.ghe_base_url}/{org.org_login}/{repo.repo_name}"
            repo_entries = service_index.get(repo_url, [])
            if repo_entries:
                tagged_count = 0
                skipped_no_files = 0
                for pr in prs:
                    pr_files = files.get(pr.pull_request_id, [])
                    if pr_files:
                        pr.services = self._match_services(repo_entries, pr_files)
                        if pr.services:
                            tagged_count += 1
                    else:
                        # Mark as "tried, no matches" so we don't re-fetch files
                        pr.services = pr.services if pr.services is not None else []
                        skipped_no_files += 1
                logger.info(
                    "Service tagging: %d tagged, %d skipped (no files), %d total",
                    tagged_count,
                    skipped_no_files,
                    len(prs),
                    org=org.org_login,
                    repo=repo.repo_name,
                )

        # 5. Batch post PRs to API (each PR already carries org/repo identity)
        if prs:
            affected = await self._post_prs_batch(prs)
            self.metrics.prs_upserted += affected
            logger.info(
                "Batch upserted %d PRs (%d affected rows)",
                len(prs),
                affected,
                org=org.org_login,
                repo=repo.repo_name,
            )

        # 6. Update tracker via API (keyed on GitHub org/repo IDs)
        await self._update_tracker(org.org_id, repo.repo_id, len(prs))

        self.metrics.repositories_processed += 1
        return len(prs)

    def _get_configured_cutoff(self) -> datetime:
        """Get cutoff date from config or use default."""
        if self.config.cutoff_date:
            try:
                return datetime.fromisoformat(self.config.cutoff_date)
            except ValueError:
                logger.warning(
                    "Invalid cutoff_date format: %s, using default",
                    self.config.cutoff_date,
                )
        return DEFAULT_CUTOFF_DATE

    def _get_page_size(self) -> int:
        """Get page size from config or use default."""
        if self.config.page_size:
            return self.config.page_size
        return DEFAULT_PAGE_SIZE

    def _get_tracker_lookback_days(self) -> int:
        """Get tracker lookback days from config or use default."""
        if self.config.tracker_lookback_days:
            return self.config.tracker_lookback_days
        return DEFAULT_TRACKER_LOOKBACK_DAYS

    def _get_max_concurrent_repos(self) -> int:
        """Get max concurrent repos from config or use default."""
        if self.config.max_concurrent_repos:
            return self.config.max_concurrent_repos
        return DEFAULT_MAX_CONCURRENT_REPOS

    def _build_service_index(
        self,
        summary_data: dict[str, Any],
    ) -> dict[str, list[tuple[str, str]]]:
        """Build an index mapping repo URLs to their service entries.

        Single pass over summary_data, grouping by git_url for O(1) lookup per repo.

        Args:
            summary_data: Parsed summary.yml from Artifactory.

        Returns:
            Dict mapping repo_url to list of (service_name, git_path) tuples.
        """
        index: dict[str, list[tuple[str, str]]] = {}
        for service_name, info in summary_data.items():
            git_url = info.get("git_url")
            if git_url:
                git_path = info.get("git_path", "/")
                index.setdefault(git_url, []).append((service_name, git_path))
        logger.info(
            "Built service index: %d repos, %d total entries",
            len(index),
            sum(len(v) for v in index.values()),
        )
        return index

    def _match_services(
        self,
        repo_entries: list[tuple[str, str]],
        pr_files: list[str],
    ) -> list[str]:
        """Match PR file paths to services using a pre-built repo index.

        For non-monorepos (single entry), the service is the entry key.
        For monorepos (multiple entries), match git_path against file paths.

        Args:
            repo_entries: Pre-built list of (service_name, git_path) for this repo.
            pr_files: List of repo-relative file paths changed in the PR.

        Returns:
            List of matched service names, or empty list if no matches.
        """
        # Non-monorepo: single entry means all files belong to that service
        if len(repo_entries) == 1:
            logger.debug(
                "Non-monorepo match: service=%s",
                repo_entries[0][0],
                pr_files=pr_files,
            )
            return [repo_entries[0][0]]

        # Monorepo: match file paths against each entry's git_path
        # Skip root ("/") entries — they represent the parent project, not a service
        matched: set[str] = set()
        for service_name, git_path in repo_entries:
            normalized_path = git_path.strip("/")
            if not normalized_path:
                logger.debug("Skipping root entry: service=%s", service_name)
                continue
            for file_path in pr_files:
                if file_path.startswith(normalized_path + "/"):
                    logger.debug(
                        "Matched: file=%s -> service=%s (git_path=%s)",
                        file_path,
                        service_name,
                        normalized_path,
                    )
                    matched.add(service_name)
                    break

        if not matched:
            logger.warning(
                "No services matched for PR files",
                pr_files=pr_files,
                repo_entries=[(name, path) for name, path in repo_entries],
            )
        else:
            logger.info(
                "Matched %d services for PR",
                len(matched),
                services=list(matched),
            )

        return list(matched) if matched else []

    def _get_request_headers(self) -> dict[str, str]:
        """Get headers with current request ID for API calls."""
        request_id = log_utils.get_task_id()
        if request_id:
            return {TASK_ID_HEADER: request_id}
        return {}

    async def _get_repos_pushed_since(self, org_id: int | None) -> datetime | None:
        """Compute the pushed_since cutoff for repo listing from the watermark.

        Returns None (full scan) when no watermark exists yet for the org.
        """
        watermark = await self._get_org_watermark(org_id)
        if watermark is None:
            return None
        buffer_minutes = (
            self.config.repo_activity_buffer_minutes
            if self.config.repo_activity_buffer_minutes is not None
            else DEFAULT_REPO_ACTIVITY_BUFFER_MINUTES
        )
        return watermark - timedelta(minutes=buffer_minutes)

    async def _get_org_watermark(self, org_id: int | None) -> datetime | None:
        """Get the last successful crawl start time for an org from the API.

        Args:
            org_id: GitHub organization ID.

        Returns:
            Watermark datetime if a record exists, None otherwise.
        """
        if org_id is None:
            return None

        try:
            response_bytes = await asyncio.to_thread(
                self.matik_client.get_request,
                f"/v1/ghe/org-crawl-tracker/{org_id}",
                None,  # params
                self._get_request_headers(),
            )
            self.metrics.api_calls_matik += 1
            response = json.loads(response_bytes)
            if not response.get("exists"):
                return None
            ts = response.get("last_crawled_at")
            return datetime.fromisoformat(ts) if ts else None
        except Exception:
            logger.exception("Failed to get org crawl watermark", org=org_id)
            return None

    async def _set_org_watermark(
        self, org_id: int | None, last_crawled_at: datetime
    ) -> None:
        """Persist the per-org crawl watermark via the API.

        Args:
            org_id: GitHub organization ID.
            last_crawled_at: Start time of this (successful) crawl.
        """
        if org_id is None:
            return

        tracker = GHEOrgCrawlTracker(
            org_id=org_id,
            last_crawled_at=last_crawled_at,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )

        try:
            await asyncio.to_thread(
                self.matik_client.post_json_request,
                "/v1/ghe/org-crawl-tracker",
                tracker.model_dump(mode="json"),
                self._get_request_headers(),
            )
            self.metrics.api_calls_matik += 1
            logger.info(
                "Updated org crawl watermark",
                org=org_id,
                watermark=last_crawled_at,
            )
        except Exception:
            logger.exception("Failed to update org crawl watermark", org=org_id)

    async def _get_tracker_cutoff(
        self, org_id: int | None, repo_id: int | None
    ) -> datetime | None:
        """Get tracker cutoff date from API.

        Args:
            org_id: GitHub organization ID.
            repo_id: GitHub repository ID.

        Returns:
            Cutoff datetime if tracker exists, None otherwise.
        """
        if org_id is None or repo_id is None:
            return None

        try:
            response_bytes = await asyncio.to_thread(
                self.matik_client.get_request,
                f"/v1/ghe/tracker/{org_id}/{repo_id}",
                None,  # params
                self._get_request_headers(),
            )
            self.metrics.api_calls_matik += 1
            response = json.loads(response_bytes)
            if not response.get("exists"):
                return None
            cutoff_str = response.get("cutoff_date")
            return datetime.fromisoformat(cutoff_str) if cutoff_str else None
        except Exception:
            logger.exception("Failed to get tracker cutoff", org=org_id, repo=repo_id)
            return None

    async def _post_prs_batch(self, prs: list[GHEPullRequest]) -> int:
        """Send PRs to the Scribe SQS queue for database writes.

        Sends one GHEPRBaseMessage per PR. The Scribe service consumes these
        messages and upserts each PR into the database.

        Args:
            prs: List of PRs to send.

        Returns:
            Number of messages sent.
        """
        for pr in prs:
            message = GHEPRBaseMessage(
                data=pr.model_dump(mode="json"), entered_at=utc_now_naive()
            )
            await self.sqs_publisher.send(message)
            self.metrics.sqs_messages_sent += 1
        return len(prs)

    async def _update_tracker(
        self, org_id: int | None, repo_id: int | None, prs_crawled: int
    ) -> None:
        """Send tracker update to the Scribe SQS queue.

        Args:
            org_id: GitHub organization ID.
            repo_id: GitHub repository ID.
            prs_crawled: Number of PRs crawled in this run.
        """
        if org_id is None or repo_id is None:
            logger.warning("Cannot update tracker: org_id or repo_id is None")
            return

        lookback_days = self._get_tracker_lookback_days()
        new_cutoff = utc_now_naive() - relativedelta(days=lookback_days)

        tracker = GHEPRTracker(
            org_id=org_id,
            repo_id=repo_id,
            cutoff_date=new_cutoff,
            prs_crawled_count=prs_crawled,
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )

        try:
            message = GHEPRTrackerBaseMessage(data=tracker.model_dump(mode="json"))
            await self.sqs_publisher.send(message)
            self.metrics.sqs_messages_sent += 1
            logger.info(
                "Queued tracker update",
                org=org_id,
                repo=repo_id,
                cutoff=new_cutoff,
                prs_crawled=prs_crawled,
            )
        except Exception:
            logger.exception("Failed to queue tracker update", org=org_id, repo=repo_id)
