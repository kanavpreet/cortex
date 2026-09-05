"""JIRA client for interacting with JIRA API."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.daos.jira_issues_dao import JiraHashInfo
from common.metrics.client_metrics import ClientMetrics
from common.metrics.jira_cache_metrics import JiraCacheMetrics
from common.models.enricher_messages import EnrichmentRequest
from common.models.jira_config import JiraConfig
from common.models.jira_issue import (
    Comment,
    Comments,
    Issue,
    IssueFields,
    IssueLink,
    IssueLinkType,
    Parent,
    Resolution,
    Status,
)
from common.queues.enrichment_publisher import EnrichmentPublisherSync
from common.utils.datetime_utils import utc_now_naive
from common.utils.jira_utils import (
    CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK,
    CUSTOM_FIELDS_TCMR_RELATED_PLANNED_END_DATE,
    CUSTOM_FIELDS_TCMR_RELATED_PLANNED_START_DATE,
    CUSTOM_FIELDS_TCMR_RELATED_SERVICES,
    aggregate_comments_for_llm,
    get_first_non_empty_string_field,
    get_string_list_field,
)
from common.utils.log_utils import get_logger

logger = get_logger(__name__)


@dataclass
class JiraHashCache:
    """Holds pre-fetched hash data for change detection.

    Used to avoid redundant LLM calls by caching summaries
    and detecting content changes via SHA256 hashes.
    """

    hashes: dict[str, JiraHashInfo] = field(default_factory=dict)
    """Key format: issue_key (e.g., 'TCMR-123')"""


@dataclass
class JiraClient:
    """Client for interacting with the JIRA API.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = JiraClient(jira_config=config.jira)

        issues = client.list_issues("project = TCMR")
    """

    jira_config: JiraConfig
    client_metrics: ClientMetrics | None = None
    """Optional metrics for tracking Jira API calls."""
    cache_metrics: JiraCacheMetrics | None = None
    """Optional metrics for tracking cache operations."""
    enrichment_publisher: EnrichmentPublisherSync | None = None
    """Publishes issue content to the Enricher SQS queue for LLM summarization."""

    _base_url: str = field(init=False)
    _auth: tuple[str, str] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the JIRA client."""
        self._base_url = self.jira_config.base_url.rstrip("/")
        self._auth = (self.jira_config.username, self.jira_config.password)

        logger.info("created JIRA client", base_url=self._base_url)

    def _get_all_issues(self, jql: str) -> list[dict[str, Any]]:
        """Retrieve all issues from JIRA matching the given JQL query.

        Handles pagination automatically.

        Args:
            jql: JQL query string

        Returns:
            List of raw JIRA issue dictionaries.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        issues: list[dict[str, Any]] = []
        start_at = 0
        max_results = self.jira_config.pagination_max_results

        logger.debug("pagination configured", max_results=max_results)

        with httpx.Client(timeout=self.jira_config.client_timeout) as client:
            while True:
                url = f"{self._base_url}/rest/api/2/search"
                endpoint = "/rest/api/2/search"
                params: dict[str, str | int] = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "expand": "issuelinks,comment",
                    "fields": "*all",
                }

                start_time = time.perf_counter()
                try:
                    response = client.get(url, params=params, auth=self._auth)
                    response.raise_for_status()
                    # Record successful request
                    if self.client_metrics:
                        duration = time.perf_counter() - start_time
                        self.client_metrics.record_request(
                            "GET", endpoint, duration, response.status_code
                        )
                except Exception as e:
                    # Record failed request
                    if self.client_metrics:
                        duration = time.perf_counter() - start_time
                        status_code = getattr(
                            getattr(e, "response", None), "status_code", 0
                        )
                        self.client_metrics.record_request(
                            "GET", endpoint, duration, status_code, e
                        )
                    raise
                data = response.json()

                result_issues = data.get("issues", [])
                total = data.get("total", 0)

                logger.info(
                    "fetched issues page",
                    fetched=len(result_issues),
                    total=total,
                )

                issues.extend(result_issues)
                start_at += len(result_issues)

                if start_at >= total or not result_issues:
                    logger.info("total issues retrieved", count=len(issues))
                    return issues

                logger.info("fetching next page", start_at=start_at)

    def list_issues_with_hash_cache(
        self,
        jql: str,
        hash_cache: JiraHashCache | None = None,
    ) -> list[Issue]:
        """Retrieve issues with hash-based LLM call optimization.

        Convenience method that fetches issues from JIRA and enriches them
        in one call. For hash-cache-aware usage, call fetch_issues() and
        enrich_issues() separately with a populated hash cache in between.

        Args:
            jql: JQL query string
            hash_cache: Pre-fetched hash data for change detection.
                        If None, an empty cache is used (all issues processed).

        Returns:
            List of Issue models with LLM-generated summaries and hash fields.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        raw_issues = self.fetch_issues(jql)
        return self.enrich_issues(raw_issues, hash_cache)

    def fetch_issues(self, jql: str) -> list[dict[str, Any]]:
        """Fetch raw issues from JIRA matching the given JQL query.

        This is the first step of the two-step fetch + enrich pattern.
        After fetching, extract issue keys and load hash cache from the
        Matik API, then pass both to enrich_issues().

        Args:
            jql: JQL query string

        Returns:
            List of raw JIRA issue dictionaries.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        return self._get_all_issues(jql)

    def get_issue(self, issue_key: str) -> dict[str, Any] | None:
        """Fetch a single issue by key.

        Thin wrapper over ``fetch_issues`` — no new HTTP logic, just a JQL search
        scoped to one key.

        Args:
            issue_key: JIRA issue key (e.g. "TCMR-1234").

        Returns:
            Raw JIRA issue dictionary if found, None if no issue matches.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        issues = self.fetch_issues(f"key = {issue_key}")
        return issues[0] if issues else None

    def _enrich_single_issue(
        self,
        raw_issue: dict[str, Any],
        hash_cache: JiraHashCache,
        issue_description_prompt: str | None,
        comments_summary_prompt: str | None,
    ) -> tuple[Issue, bool]:
        """Publish a single issue's content to the Enricher for summarization.

        The Enricher owns LLM summarization and hash-based change detection, so
        the raw content is forwarded unconditionally. ``hash_cache`` is used
        only to determine whether the issue is new (for stats logging).

        Called concurrently by enrich_issues(). Thread-safe: hash_cache is
        read-only and enrichment_publisher is stateless between calls.

        Returns:
            Tuple of (issue as converted from raw JIRA data, was_new).
        """
        issue = self._convert_issue(raw_issue)
        issue_key = issue.key or ""

        current_summary = issue.fields.summary if issue.fields else ""
        comments = issue.fields.comments if issue.fields else None
        comment_bodies = (
            [c.body for c in (comments.comments or []) if c.body] if comments else []
        )
        aggregated_comments = aggregate_comments_for_llm(comment_bodies)

        content: dict[str, str] = {}
        if issue_description_prompt and current_summary:
            content["issue_description"] = current_summary
        if comments_summary_prompt and aggregated_comments:
            content["aggregated_comments"] = aggregated_comments

        if content and self.enrichment_publisher is not None:
            import uuid

            enrichment_msg = EnrichmentRequest(
                source_type="jira",
                producer="historian",
                task_id=str(uuid.uuid4()),
                entity_id={"issue_key": issue_key},
                content=content,
                entered_at=utc_now_naive(),
            )
            self.enrichment_publisher.publish(enrichment_msg)

        was_new = issue_key not in hash_cache.hashes
        return issue, was_new

    def enrich_issues(
        self,
        raw_issues: list[dict[str, Any]],
        hash_cache: JiraHashCache | None = None,
    ) -> list[Issue]:
        """Publish raw JIRA issues to the Enricher for LLM summarization.

        Publishes one enrichment request per issue (the Enricher owns LLM
        summarization and hash-based change detection). Requests are published
        concurrently up to llm_concurrency workers. This is the second step of
        the two-step fetch + enrich pattern.

        Args:
            raw_issues: Raw JIRA issue dicts from fetch_issues()
            hash_cache: Pre-fetched hash data, used only to distinguish new
                        issues for stats logging. If None, all issues count as new.

        Returns:
            List of Issue models (base fields only; summaries are filled in
            asynchronously by the Enricher), in the same order as raw_issues.
        """
        if hash_cache is None:
            hash_cache = JiraHashCache()

        issue_description_prompt = self.jira_config.llm_description_prompt
        if not issue_description_prompt:
            logger.warning("LLM description prompt not configured")

        comments_summary_prompt = self.jira_config.llm_comments_prompt
        if not comments_summary_prompt:
            logger.warning("LLM comments prompt not configured")

        new_issues = 0

        results: dict[int, Issue] = {}
        max_workers = self.jira_config.llm_concurrency

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._enrich_single_issue,
                    raw_issue,
                    hash_cache,
                    issue_description_prompt,
                    comments_summary_prompt,
                ): idx
                for idx, raw_issue in enumerate(raw_issues)
            }
            for future in as_completed(futures):
                idx = futures[future]
                issue, was_new = future.result()
                results[idx] = issue
                if was_new:
                    new_issues += 1

        result_issues = [results[i] for i in range(len(raw_issues))]

        logger.info(
            "Jira processing complete",
            total=len(result_issues),
            new_issues=new_issues,
        )

        if self.cache_metrics:
            self.cache_metrics.record_crawler_stats(
                issues_processed=len(result_issues),
                facade_calls=0,
            )

        return result_issues

    def _convert_issue(self, raw: dict[str, Any]) -> Issue:
        """Convert raw JIRA API issue to Issue model."""
        raw_fields = raw.get("fields", {})

        # Convert status
        status: Status | None = None
        raw_status = raw_fields.get("status")
        if raw_status:
            status = Status(
                self=raw_status.get("self", ""),
                id=raw_status.get("id", ""),
                name=raw_status.get("name", ""),
            )

        # Convert resolution
        resolution: Resolution | None = None
        raw_resolution = raw_fields.get("resolution")
        if raw_resolution:
            resolution = Resolution(
                self=raw_resolution.get("self", ""),
                id=raw_resolution.get("id", ""),
                name=raw_resolution.get("name", ""),
            )

        # Convert parent
        parent: Parent | None = None
        raw_parent = raw_fields.get("parent")
        if raw_parent:
            parent = Parent(
                id=raw_parent.get("id"),
                key=raw_parent.get("key"),
            )

        # Convert comments
        comments: Comments | None = None
        raw_comment = raw_fields.get("comment")
        if raw_comment:
            comment_list = raw_comment.get("comments", [])
            comments = Comments(
                comments=[
                    Comment(
                        id=c.get("id"),
                        self=c.get("self"),
                        name=c.get("author", {}).get("name"),
                        body=c.get("body"),
                        created=c.get("created"),
                        updated=c.get("updated"),
                    )
                    for c in comment_list
                ]
            )

        # Convert issue links
        issuelinks: list[IssueLink] | None = None
        raw_issuelinks = raw_fields.get("issuelinks")
        if raw_issuelinks:
            issuelinks = []
            for link in raw_issuelinks:
                link_type = link.get("type", {})
                issuelinks.append(
                    IssueLink(
                        id=link.get("id"),
                        self=link.get("self"),
                        type=IssueLinkType(
                            id=link_type.get("id"),
                            self=link_type.get("self"),
                            name=link_type.get("name", ""),
                            inward=link_type.get("inward", ""),
                            outward=link_type.get("outward", ""),
                        ),
                    )
                )

        # Build fields
        fields = IssueFields(
            summary=raw_fields.get("summary"),
            environment=raw_fields.get("environment"),
            status=status,
            resolution=resolution,
            created=raw_fields.get("created"),
            updated=raw_fields.get("updated"),
            duedate=raw_fields.get("duedate"),
            resolutiondate=raw_fields.get("resolutiondate"),
            labels=raw_fields.get("labels"),
            issuelinks=issuelinks,
            comments=comments,
            parent=parent,
        )

        # Extract custom fields
        tcmr_git_pr_link = get_first_non_empty_string_field(
            raw_fields, CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK
        )
        tcmr_services = get_string_list_field(
            raw_fields, CUSTOM_FIELDS_TCMR_RELATED_SERVICES
        )
        tcmr_planned_start = get_first_non_empty_string_field(
            raw_fields, CUSTOM_FIELDS_TCMR_RELATED_PLANNED_START_DATE
        )
        tcmr_planned_end = get_first_non_empty_string_field(
            raw_fields, CUSTOM_FIELDS_TCMR_RELATED_PLANNED_END_DATE
        )

        return Issue(
            id=raw.get("id"),
            self=raw.get("self"),
            key=raw.get("key"),
            fields=fields,
            tcmr_related_git_pr_link=tcmr_git_pr_link or None,
            tcmr_related_services=tcmr_services,
            tcmr_planned_start_date=tcmr_planned_start or None,
            tcmr_planned_end_date=tcmr_planned_end or None,
        )


def create_jira_client(
    jira_config: JiraConfig,
    client_metrics: ClientMetrics | None = None,
    cache_metrics: JiraCacheMetrics | None = None,
    enrichment_publisher: EnrichmentPublisherSync | None = None,
) -> JiraClient:
    """Create a JIRA client from configuration objects.

    Args:
        jira_config: JiraConfig from MatikConfig.jira
        client_metrics: ClientMetrics for tracking Jira API calls (optional)
        cache_metrics: JiraCacheMetrics for tracking cache operations (optional)
        enrichment_publisher: Publishes issue content to the Enricher SQS queue

    Returns:
        Configured JiraClient instance.

    Example:
        from common.config import load_config
        from common.metrics import TelescopeClient
        from common.metrics.client_metrics import ClientMetrics
        from common.metrics.jira_cache_metrics import JiraCacheMetrics

        config = load_config("config/matik-service-config.yml")

        # With metrics
        telescope = TelescopeClient(config.telescope)
        client_metrics = ClientMetrics(telescope.meter, "historian", "jira")
        cache_metrics = JiraCacheMetrics(telescope.meter, "historian")
        client = create_jira_client(
            jira_config=config.jira,
            client_metrics=client_metrics,
            cache_metrics=cache_metrics,
        )
    """
    return JiraClient(
        jira_config=jira_config,
        client_metrics=client_metrics,
        cache_metrics=cache_metrics,
        enrichment_publisher=enrichment_publisher,
    )
