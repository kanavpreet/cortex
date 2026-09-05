"""GitHub Enterprise client for interacting with GHE API using githubkit."""

import asyncio
import base64
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar, cast

from githubkit import AppInstallationAuthStrategy, GitHub
from githubkit.exception import RequestFailed
from githubkit.versions.latest.models import OrganizationSimple
from pydantic import BaseModel

from common.metrics.client_metrics import ClientMetrics
from common.metrics.ghe_cache_metrics import GHECacheMetrics
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.enricher_messages import EnrichmentRequest
from common.models.ghe_pr import GHEPullRequest
from common.queues.enrichment_publisher import EnrichmentPublisher
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive
from common.utils.env_utils import determine_environment
from common.utils.github_utils import (
    build_ghe_pr_content,
    is_github_rate_limit_error,
    is_ingestible_pr_comment,
)
from common.utils.jira_utils import extract_tcmr_key

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryConfig(BaseModel):
    """Configuration for retry behavior on GHE API calls."""

    enabled: bool = True
    """Whether retries are enabled."""

    backoff_durations: list[float] = [2.0, 4.0, 8.0, 15.0, 30.0]
    """Backoff durations in seconds between retry attempts."""

    rate_limit_wait: float = 3600.0
    """Time to wait in seconds when rate limited (default: 1 hour)."""


# Default retry configuration for production use
DEFAULT_RETRY_CONFIG = RetryConfig()


@dataclass
class GHEOrg:
    """Transient in-memory GitHub organization identity (not persisted).

    Org/repo are no longer separate tables; this carries just the identity
    the client needs to fetch repos/PRs and to stamp onto each PR row.
    """

    org_id: int
    org_login: str


@dataclass
class GHERepo:
    """Transient in-memory GitHub repository identity (not persisted)."""

    repo_id: int
    repo_name: str
    org_id: int
    # Last push to any branch (UTC). Used to skip repos with no activity since
    # the last crawl. None when the API does not report it.
    pushed_at: datetime | None = None
    archived: bool = False


@dataclass
class RawPullRequest:
    """Minimal PR data extracted from raw GitHub API response.

    Used to avoid strict Pydantic validation on fields we don't use
    (e.g., requested_teams which may have different schema on GHE).
    """

    id: int
    number: int
    title: str | None
    body: str | None
    state: str
    locked: bool | None
    created_at: datetime | None
    updated_at: datetime | None
    closed_at: datetime | None
    merged_at: datetime | None
    base_ref: str | None


@dataclass
class PRHashInfo:
    """Contains the hash and summary for change detection.

    Mirrors daos.PRHashInfo for client-side usage.
    """

    pull_request_id: int
    repository_id: int
    description_hash: str
    pull_request_summary: str
    services: list[str] | None = None


@dataclass
class PRHashCache:
    """Holds pre-fetched hash data for change detection."""

    hashes: dict[str, PRHashInfo] = field(default_factory=dict)
    """Key format: str(pull_request_id)"""


@dataclass
class PRFileInfo:
    """Metadata for a single file changed in a pull request."""

    filename: str
    status: str
    previous_filename: str | None = None


@dataclass
class PRWithFiles:
    """Pairs a GHEPullRequest with its list of changed file paths.

    File paths are repo-relative (e.g. 'src/common/clients/ghe_client.py').
    Kept in memory only for downstream service matching.
    """

    pr: GHEPullRequest
    files: list[str] = field(default_factory=list)


def _get_private_key(ghe_config: BiztechGitHubConfig) -> str:
    """Get the private key from config or file."""
    if ghe_config.ghe_app_private_key:
        return base64.standard_b64decode(ghe_config.ghe_app_private_key).decode("utf-8")
    elif ghe_config.ghe_app_private_key_file_name:
        with open(ghe_config.ghe_app_private_key_file_name, "rb") as f:
            return f.read().decode("utf-8")
    else:
        try:
            with open("ghe_key_pcs8.pem", "rb") as f:
                return f.read().decode("utf-8")
        except FileNotFoundError as e:
            logger.error("Failed to read default private key file")
            raise ValueError(
                "No private key provided and default file not found"
            ) from e


@dataclass
class GHEClient:
    """Async client for interacting with GitHub Enterprise API.

    Supports GitHub App authentication and provides async methods for
    fetching organizations, repositories, and pull requests.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = GHEClient(ghe_config=config.biztech_github)

        orgs = await client.get_orgs()
        repos = await client.get_repos(orgs[0])
    """

    ghe_config: BiztechGitHubConfig
    retry_config: RetryConfig = field(default_factory=lambda: DEFAULT_RETRY_CONFIG)
    max_concurrent_pr_tasks: int = 50
    """Maximum number of concurrent PR processing tasks."""
    client_metrics: ClientMetrics | None = None
    """Optional metrics for tracking GHE API calls."""
    cache_metrics: GHECacheMetrics | None = None
    """Optional metrics for tracking cache operations."""
    enrichment_publisher: EnrichmentPublisher | None = None
    """When set, publishes to SQS instead of calling Facade directly."""

    _github: GitHub[AppInstallationAuthStrategy] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the GitHub client with App authentication."""
        logger.debug(
            "Creating GHE client with base_url: %s", self.ghe_config.ghe_base_url
        )

        private_key = _get_private_key(self.ghe_config)
        app_id = int(self.ghe_config.ghe_app_id)
        installation_id = int(self.ghe_config.ghe_app_installation_id)

        logger.info("GHE_APP_ID: %d", app_id)
        logger.info("GHE_INSTALLATION_ID: %d", installation_id)

        # Create GitHub client with App Installation authentication
        self._github = GitHub(
            AppInstallationAuthStrategy(
                app_id=app_id,
                private_key=private_key,
                installation_id=installation_id,
            ),
            base_url=self.ghe_config.ghe_api_url,
        )

    async def close(self) -> None:
        """Close the GitHub client and release resources.

        Call this when done using the client to properly close HTTP connections.
        Handles the case where the async client was never initialized.
        """
        try:
            if hasattr(self._github, "__aexit__"):
                await self._github.__aexit__(None, None, None)
            elif hasattr(self._github, "close"):
                await self._github.close()
        except AttributeError:
            # githubkit's async client may not be initialized if no async
            # requests were made - this is safe to ignore
            pass

    async def _retry_with_backoff(
        self,
        operation: Callable[[], Coroutine[Any, Any, T]],
        operation_name: str,
        metric_name: str | None = None,
    ) -> T:
        """Wrap an async API call with retry logic.

        Args:
            operation: A callable that returns a fresh coroutine for each invocation.
                       Must be a zero-argument function that returns an awaitable.
            operation_name: Detailed name for logging (may include repo/PR info).
            metric_name: Short, low-cardinality name for metrics labels.
                         Defaults to operation_name if not provided.

        Returns:
            The result of the operation.

        Raises:
            Exception: If all retry attempts fail.
        """
        endpoint = metric_name or operation_name
        start_time = time.perf_counter()
        try:
            result = await operation()
            # Record successful request
            if self.client_metrics:
                duration = time.perf_counter() - start_time
                self.client_metrics.record_request("GET", endpoint, duration, 200)
            return result
        except Exception as err:
            if not self.retry_config.enabled:
                logger.debug(
                    "%s: Retries disabled, returning error immediately",
                    operation_name,
                )
                # Record failed request
                if self.client_metrics:
                    duration = time.perf_counter() - start_time
                    self.client_metrics.record_request(
                        "GET", endpoint, duration, 500, err
                    )
                raise

            # Check for rate limit error (403/429, RateLimitExceeded, or message pattern)
            if is_github_rate_limit_error(err):
                logger.warning(
                    "%s: Rate limit encountered, waiting %.0fs before retry",
                    operation_name,
                    self.retry_config.rate_limit_wait,
                )
                # Record rate limit event
                if self.cache_metrics:
                    self.cache_metrics.record_rate_limit(operation_name)
                await asyncio.sleep(self.retry_config.rate_limit_wait)

                try:
                    result = await operation()
                    if self.client_metrics:
                        duration = time.perf_counter() - start_time
                        self.client_metrics.record_request(
                            "GET", endpoint, duration, 200
                        )
                        self.client_metrics.record_retry("GET", endpoint, 1)
                    return result
                except Exception as retry_err:
                    logger.error(
                        "%s: Failed after rate limit retry: %s",
                        operation_name,
                        retry_err,
                    )
                    if self.client_metrics:
                        duration = time.perf_counter() - start_time
                        self.client_metrics.record_request(
                            "GET", endpoint, duration, 500, retry_err
                        )
                    raise

            # Retry with exponential backoff for other errors
            last_err = err
            for attempt, duration in enumerate(self.retry_config.backoff_durations):
                logger.warning(
                    "%s: Attempt %d failed, retrying in %.1fs: %s",
                    operation_name,
                    attempt + 1,
                    duration,
                    err,
                )
                # Record retry attempt
                if self.client_metrics:
                    self.client_metrics.record_retry("GET", endpoint, attempt + 1)
                await asyncio.sleep(duration)

                try:
                    result = await operation()
                    if self.client_metrics:
                        elapsed = time.perf_counter() - start_time
                        self.client_metrics.record_request(
                            "GET", endpoint, elapsed, 200
                        )
                    return result
                except Exception as retry_err:
                    # Check if this is a rate limit error during retry
                    if is_github_rate_limit_error(retry_err):
                        logger.warning(
                            "%s: Rate limit during retry, waiting %.0fs",
                            operation_name,
                            self.retry_config.rate_limit_wait,
                        )
                        if self.cache_metrics:
                            self.cache_metrics.record_rate_limit(operation_name)
                        await asyncio.sleep(self.retry_config.rate_limit_wait)

                        try:
                            result = await operation()
                            if self.client_metrics:
                                elapsed = time.perf_counter() - start_time
                                self.client_metrics.record_request(
                                    "GET", endpoint, elapsed, 200
                                )
                            return result
                        except Exception as rl_err:
                            last_err = rl_err
                            break
                    last_err = retry_err

            # Record final failure
            if self.client_metrics:
                elapsed = time.perf_counter() - start_time
                self.client_metrics.record_request(
                    "GET", endpoint, elapsed, 500, last_err
                )

            logger.error(
                "%s: All retry attempts exhausted: %s", operation_name, last_err
            )
            raise last_err from last_err

    async def get_orgs(self) -> list[GHEOrg]:
        """Get all organizations accessible to the GitHub App.

        Returns:
            List of GHEOrg identities.
        """
        orgs: list[OrganizationSimple] = []

        org: OrganizationSimple
        async for org in self._github.paginate(
            self._github.rest.orgs.async_list,
        ):
            orgs.append(org)

        return [GHEOrg(org_id=org.id, org_login=org.login) for org in orgs]

    async def get_organization(self, org_name: str) -> GHEOrg:
        """Get a specific organization by name.

        Args:
            org_name: Organization login name.

        Returns:
            GHEOrg identity.

        Raises:
            RequestFailed: If the organization is not found or API fails.
        """
        response = await self._retry_with_backoff(
            lambda: self._github.rest.orgs.async_get(org=org_name),
            f"get_organization({org_name})",
            metric_name="get_organization",
        )
        org = response.parsed_data

        org_model = GHEOrg(org_id=org.id, org_login=org.login)
        logger.info("Fetched organization: %s", org_model)

        return org_model

    async def get_repos(
        self,
        org: GHEOrg,
        page_size: int = 100,
        pushed_since: datetime | None = None,
    ) -> list[GHERepo]:
        """Get active repositories for an organization.

        Archived repositories are always skipped (they cannot receive new
        merges). When ``pushed_since`` is provided, repositories last pushed
        before it are skipped and pagination stops early, since the listing is
        sorted by ``pushed`` descending.

        Args:
            org: GHEOrg to fetch repos for.
            page_size: Number of repos per page (max 100).
            pushed_since: Only return repos pushed on or after this time. None
                lists every (non-archived) repo.

        Returns:
            List of GHERepo identities.
        """
        if page_size > 100:
            page_size = 100

        normalized_since: datetime | None = None
        if pushed_since is not None:
            normalized_since = parse_timestamp_to_utc(pushed_since)

        logger.debug("Page size: %d", page_size)
        logger.info(
            "Fetching repositories for organization: %s (pushed_since=%s)",
            org.org_login,
            normalized_since,
        )

        model_repos: list[GHERepo] = []
        skipped_archived = 0
        skipped_dormant = 0
        page = 1

        while True:
            # Bind page value via default argument to avoid closure issue
            async def fetch_repos_page(p: int = page) -> Any:
                # Sort by last push descending so we can stop paging once we
                # reach repos older than the cutoff.
                return await self._github.rest.repos.async_list_for_org(
                    org=org.org_login,
                    type="all",
                    sort="pushed",
                    direction="desc",
                    per_page=page_size,
                    page=p,
                )

            response = await self._retry_with_backoff(
                fetch_repos_page,
                f"get_repos({org.org_login}, page={page})",
                metric_name="get_repos",
            )

            repos = response.parsed_data
            if not repos:
                break  # No more results

            for repo in repos:
                pushed_at = parse_timestamp_to_utc(repo.pushed_at)
                if repo.archived:
                    skipped_archived += 1
                    continue
                if (
                    normalized_since is not None
                    and pushed_at is not None
                    and pushed_at < normalized_since
                ):
                    skipped_dormant += 1
                    continue
                model_repos.append(
                    GHERepo(
                        repo_id=repo.id,
                        repo_name=repo.name,
                        org_id=org.org_id,
                        pushed_at=pushed_at,
                        archived=bool(repo.archived),
                    )
                )

            # Early exit: if the oldest repo on this page was pushed before the
            # cutoff, no later page can have relevant repos (sorted pushed desc).
            if normalized_since is not None:
                oldest_pushed = parse_timestamp_to_utc(repos[-1].pushed_at)
                if oldest_pushed is not None and oldest_pushed < normalized_since:
                    logger.info(
                        "Early exit: oldest repo on page %d (pushed %s) is before "
                        "cutoff (%s)",
                        page,
                        oldest_pushed,
                        normalized_since,
                    )
                    break

            # If we got fewer results than page_size, we're done
            if len(repos) < page_size:
                break

            page += 1

        logger.info(
            "Fetched %d active repos for %s (skipped %d archived, %d dormant)",
            len(model_repos),
            org.org_login,
            skipped_archived,
            skipped_dormant,
        )
        return model_repos

    def _parse_raw_pr(self, pr_data: dict[str, Any]) -> RawPullRequest:
        """Parse raw PR JSON into RawPullRequest dataclass.

        This avoids strict Pydantic validation on fields we don't use
        (e.g., requested_teams which may have different schema on GHE).
        """
        base = pr_data.get("base", {})
        return RawPullRequest(
            id=pr_data["id"],
            number=pr_data["number"],
            title=pr_data.get("title"),
            body=pr_data.get("body"),
            state=pr_data.get("state", ""),
            locked=pr_data.get("locked"),
            created_at=parse_timestamp_to_utc(pr_data.get("created_at")),
            updated_at=parse_timestamp_to_utc(pr_data.get("updated_at")),
            closed_at=parse_timestamp_to_utc(pr_data.get("closed_at")),
            merged_at=parse_timestamp_to_utc(pr_data.get("merged_at")),
            base_ref=base.get("ref") if base else None,
        )

    async def get_pull_request(
        self, org: str, repo: str, pr_number: int
    ) -> RawPullRequest | None:
        """Get a single pull request by number.

        Unlike other GHE methods, this does NOT go through ``_retry_with_backoff``
        — a 404 here means the cited PR number doesn't exist (a hallucination-guard
        check, not a transient failure), so retrying it would just waste time before
        still returning ``None``. Other errors still propagate.

        Args:
            org: Organization name.
            repo: Repository name.
            pr_number: PR number (not internal ID).

        Returns:
            RawPullRequest if found, None if the PR doesn't exist (404).

        Raises:
            githubkit.exception.RequestFailed: On any non-404 API error.
        """
        try:
            response = await self._github.rest.pulls.async_get(
                owner=org, repo=repo, pull_number=pr_number
            )
        except RequestFailed as err:
            status_code = getattr(err.response, "status_code", None)
            if status_code == 404:
                logger.info("pull request not found: %s/%s#%d", org, repo, pr_number)
                return None
            raise

        # Use raw JSON to avoid strict Pydantic validation on unused fields
        return self._parse_raw_pr(cast("dict[str, Any]", response.json()))

    async def get_pull_requests(
        self,
        org: str,
        repo: str,
        page_size: int = 100,
        cutoff_date: datetime | None = None,
    ) -> list[RawPullRequest]:
        """Get merged pull requests for a repository.

        Only returns PRs that were merged on or after the cutoff date.

        Args:
            org: Organization name.
            repo: Repository name.
            page_size: Number of PRs per page (max 100).
            cutoff_date: Only return PRs merged on or after this date.

        Returns:
            List of RawPullRequest objects (minimal data extracted from raw JSON).
        """
        if page_size > 100:
            page_size = 100

        logger.debug("Page size: %d", page_size)
        logger.info("Fetching PRs for organization %s, repo %s", org, repo)

        # Normalize cutoff_date upfront if provided
        normalized_cutoff: datetime | None = None
        if cutoff_date is not None:
            normalized_cutoff = parse_timestamp_to_utc(cutoff_date)

        all_prs: list[RawPullRequest] = []
        page = 1

        while True:
            # Bind page value via default argument to avoid closure issue
            async def fetch_prs_page(p: int = page) -> Any:
                return await self._github.rest.pulls.async_list(
                    owner=org,
                    repo=repo,
                    state="closed",
                    sort="updated",
                    direction="desc",
                    per_page=page_size,
                    page=p,
                )

            response = await self._retry_with_backoff(
                fetch_prs_page,
                f"get_pull_requests({org}/{repo}, page={page})",
                metric_name="get_pull_requests",
            )
            # Use raw JSON to avoid strict Pydantic validation on unused fields
            raw_data: list[dict[str, Any]] = response.json()
            page_prs = [self._parse_raw_pr(pr) for pr in raw_data]

            if not page_prs:
                break  # No more results

            # Filter every page: only merged PRs with merged_at >= cutoff
            for pr in page_prs:
                if not pr.merged_at:
                    continue
                if normalized_cutoff is not None and pr.merged_at < normalized_cutoff:
                    continue
                all_prs.append(pr)

            # Early exit: if the oldest PR on this page was updated before
            # the cutoff, no subsequent pages will have relevant PRs either
            # (API sorts by updated desc)
            if normalized_cutoff is not None:
                oldest_pr = page_prs[-1]  # Last PR is oldest (desc order)
                oldest_updated = oldest_pr.updated_at
                if oldest_updated and oldest_updated < normalized_cutoff:
                    logger.info(
                        "Early exit: oldest PR on page %d (updated %s) is before cutoff (%s)",
                        page,
                        oldest_updated,
                        normalized_cutoff,
                    )
                    break

            page += 1

        return all_prs

    async def get_pr_files(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        per_page: int = 100,
    ) -> list[PRFileInfo]:
        """Get files changed in a pull request.

        Args:
            owner: Repository owner (organization).
            repo: Repository name.
            pull_number: PR number (not internal ID).
            per_page: Number of files per page (max 100).

        Returns:
            List of PRFileInfo with file metadata.
        """
        if per_page > 100:
            per_page = 100

        all_files: list[PRFileInfo] = []
        page = 1

        while True:

            async def fetch_files_page(p: int = page) -> Any:
                return await self._github.rest.pulls.async_list_files(
                    owner=owner,
                    repo=repo,
                    pull_number=pull_number,
                    per_page=per_page,
                    page=p,
                )

            response = await self._retry_with_backoff(
                fetch_files_page,
                f"get_pr_files({owner}/{repo}#{pull_number}, page={page})",
                metric_name="get_pr_files",
            )

            files_data: list[dict[str, Any]] = response.json()
            if not files_data:
                break

            for f in files_data:
                filename = f.get("filename", "")
                if filename:
                    all_files.append(
                        PRFileInfo(
                            filename=filename,
                            status=f.get("status", ""),
                            previous_filename=f.get("previous_filename"),
                        )
                    )

            if len(files_data) < per_page:
                break

            page += 1

        return all_files

    async def get_pr_comments(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        per_page: int = 100,
    ) -> list[str]:
        """Get signal-bearing bot comments on a pull request.

        PRs are issues in GitHub's data model, so the PR conversation thread is
        fetched via the issue comments endpoint (GitHub's formal PR *reviews* /
        inline diff comments are a separate endpoint and excluded). Of the
        conversation comments, only those that carry durable signal are kept:
        the jenkins-prod AirChat "Review Summary" and all spacelift-prod
        plan/apply comments (see :func:`is_ingestible_pr_comment`). All other
        comments (humans, other bots) are dropped.

        Args:
            owner: Repository owner (organization).
            repo: Repository name.
            pull_number: PR number (not internal ID).
            per_page: Number of comments per page (max 100).

        Returns:
            Kept comment body strings, in chronological order.
        """
        if per_page > 100:
            per_page = 100

        all_comments: list[str] = []
        page = 1

        while True:

            async def fetch_comments_page(p: int = page) -> Any:
                return await self._github.rest.issues.async_list_comments(
                    owner=owner,
                    repo=repo,
                    issue_number=pull_number,
                    per_page=per_page,
                    page=p,
                )

            response = await self._retry_with_backoff(
                fetch_comments_page,
                f"get_pr_comments({owner}/{repo}#{pull_number}, page={page})",
                metric_name="get_pr_comments",
            )

            comments_data: list[dict[str, Any]] = response.json()
            if not comments_data:
                break

            for c in comments_data:
                body = c.get("body")
                if not body or not body.strip():
                    continue
                author = (c.get("user") or {}).get("login", "") or ""
                if is_ingestible_pr_comment(
                    author,
                    body,
                    self.ghe_config.ingestible_comment_bots,
                    self.ghe_config.jenkins_review_summary_title,
                ):
                    all_comments.append(body)

            if len(comments_data) < per_page:
                break

            page += 1

        return all_comments

    async def list_pull_requests_with_hash_cache(
        self,
        org: GHEOrg,
        repo: GHERepo,
        page_size: int = 100,
        cutoff_date: datetime | None = None,
        hash_cache: PRHashCache | None = None,
    ) -> list[PRWithFiles]:
        """Process PRs and publish each to the Enricher for summarization.

        Publishes an enrichment request per PR (the Enricher owns LLM
        summarization and hash-based change detection) and fetches files for new
        PRs or PRs missing service info.

        Args:
            org: GHEOrg identity.
            repo: GHERepo identity.
            page_size: Number of PRs per page (max 100).
            cutoff_date: Only return PRs created on or after this date.
            hash_cache: Pre-fetched hash data for file/service-fetch gating.

        Returns:
            List of PRWithFiles (each containing a GHEPullRequest and its files).
        """
        if hash_cache is None:
            hash_cache = PRHashCache()

        logger.info(
            "Using cutoff date: %s, hash cache size: %d",
            cutoff_date,
            len(hash_cache.hashes),
        )

        prs = await self.get_pull_requests(
            org.org_login, repo.repo_name, page_size, cutoff_date
        )

        # Process PRs concurrently with bounded concurrency
        semaphore = asyncio.Semaphore(self.max_concurrent_pr_tasks)

        async def bounded_process(
            pr: RawPullRequest,
        ) -> tuple[GHEPullRequest, bool, bool, list[str]]:
            async with semaphore:
                return await self._process_pr_with_cache(pr, org, repo, hash_cache)

        tasks = [bounded_process(pr) for pr in prs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_prs = 0
        pr_results: list[PRWithFiles] = []
        files_fetched_count = 0

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("Failed to process PR %d: %s", prs[i].id, result)
            else:
                ghe_pr, _was_cache_hit, was_new, files = result
                pr_results.append(PRWithFiles(pr=ghe_pr, files=files))
                if files:
                    files_fetched_count += 1
                if was_new:
                    new_prs += 1

        logger.info(
            "PR processing complete: total=%d, new_prs=%d, files_fetched=%d",
            len(pr_results),
            new_prs,
            files_fetched_count,
        )

        return pr_results

    async def _process_pr_with_cache(
        self,
        pr: RawPullRequest,
        org: GHEOrg,
        repo: GHERepo,
        hash_cache: PRHashCache,
    ) -> tuple[GHEPullRequest, bool, bool, list[str]]:
        """Process a single PR: publish it to the Enricher and fetch its files.

        The base (summary-less) record is written by the crawler via Scribe; the
        Enricher fills in ``pull_request_summary``/``description_hash`` later. The
        ``hash_cache`` is used only to gate file/service fetching and preserve
        previously resolved services.

        Returns:
            Tuple of (GHEPullRequest, was_cache_hit, was_new, files). ``was_cache_hit``
            is always False (kept for the caller's tuple shape).
        """
        original_description = pr.body or ""
        title = pr.title or ""

        # Fetch the PR conversation thread and fold it into the summarization
        # input. Fetched for every crawled PR (not only new ones) so the content
        # we hash/summarize stays consistent across crawls: gating on "new only"
        # would thrash the hash and regress the summary whenever a PR is re-seen.
        try:
            comment_bodies = await self.get_pr_comments(
                org.org_login, repo.repo_name, pr.number
            )
        except Exception as e:
            logger.warning("PR %d: failed to fetch comments: %s", pr.id, e)
            comment_bodies = []

        # Prepend the title and append the comment thread to the description as a
        # single field: "<title>;<description>;comments:<c1>;<c2>;...". This is
        # the string that gets hashed and summarized.
        combined_description = build_ghe_pr_content(
            title, original_description, comment_bodies
        )

        ghe_pr = GHEPullRequest(
            pull_request_id=pr.id,
            pull_request_number=pr.number,
            org_id=org.org_id,
            org_login=org.org_login,
            repo_id=repo.repo_id,
            repo_name=repo.repo_name,
            merged=pr.merged_at is not None,
            state=pr.state,
            locked=pr.locked or False,
            created_at=pr.created_at or utc_now_naive(),
            closed_at=pr.closed_at,
            merged_at=pr.merged_at,
            last_updated_at=pr.updated_at,
            target_branch_name=pr.base_ref or "",
        )

        # Parse the declared TCMR link from the authored PR content (title + body,
        # not bot comments). Set unconditionally here — like environment below —
        # so it is captured on every ingestion path.
        ghe_pr.jira_tcmr_key = (
            extract_tcmr_key(f"{title}\n{original_description}") or None
        )

        cache_key = str(ghe_pr.pull_request_id)
        existing_info = hash_cache.hashes.get(cache_key)

        # Publish the PR for asynchronous enrichment. The Enricher owns LLM
        # summarization and hash-based change detection, so the raw content is
        # forwarded unconditionally.
        if self.enrichment_publisher:
            import uuid

            enrichment_msg = EnrichmentRequest(
                source_type="ghe_pr",
                producer="historian",
                task_id=str(uuid.uuid4()),
                entity_id={
                    "org_id": org.org_id,
                    "repository_id": repo.repo_id,
                    "pull_request_id": ghe_pr.pull_request_id,
                },
                content={
                    "original_description": combined_description,
                },
                entered_at=utc_now_naive(),
            )
            await self.enrichment_publisher.publish(enrichment_msg)

        was_cache_hit = False
        was_new = existing_info is None
        ghe_pr.environment = determine_environment(ghe_pr.target_branch_name)

        # Fetch files when PR is new or services have never been resolved.
        # services=None means "never tried", services=[] means "tried, no matches".
        files: list[str] = []
        should_fetch_files = existing_info is None or existing_info.services is None
        if should_fetch_files:
            try:
                file_infos = await self.get_pr_files(
                    org.org_login, repo.repo_name, pr.number
                )
                files = [fi.filename for fi in file_infos]
                logger.info(
                    "PR %d: fetched %d files",
                    ghe_pr.pull_request_id,
                    len(files),
                )
            except Exception as e:
                logger.warning(
                    "PR %d: failed to fetch files: %s",
                    ghe_pr.pull_request_id,
                    e,
                )
        else:
            # Preserve existing services so the upsert doesn't overwrite them with [].
            # Without this, the crawler's service tagging loop converts None → []
            # for PRs with no files returned, wiping previously stored services.
            # existing_info is guaranteed non-None here: should_fetch_files is False
            # only when existing_info is not None and existing_info.services is not None.
            assert existing_info is not None
            ghe_pr.services = existing_info.services

        return ghe_pr, was_cache_hit, was_new, files


def create_ghe_client(
    ghe_config: BiztechGitHubConfig,
    retry_config: RetryConfig | None = None,
    client_metrics: ClientMetrics | None = None,
    cache_metrics: GHECacheMetrics | None = None,
    enrichment_publisher: EnrichmentPublisher | None = None,
) -> GHEClient:
    """Create an async GHE client from configuration objects.

    Args:
        ghe_config: BiztechGitHubConfig from MatikConfig.biztech_github
        retry_config: Custom retry configuration (optional)
        client_metrics: ClientMetrics for tracking GHE API calls (optional)
        cache_metrics: GHECacheMetrics for tracking cache operations (optional)
        enrichment_publisher: When set, publishes PRs to the Enricher SQS queue

    Returns:
        Configured GHEClient instance (async).

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_ghe_client(ghe_config=config.biztech_github)
    """
    return GHEClient(
        ghe_config=ghe_config,
        retry_config=retry_config or DEFAULT_RETRY_CONFIG,
        client_metrics=client_metrics,
        cache_metrics=cache_metrics,
        enrichment_publisher=enrichment_publisher,
    )
