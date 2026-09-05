"""Jira Historian cron job entry point."""

import json
from dataclasses import dataclass
from datetime import timedelta

import boto3

from common.clients.jira_client import JiraClient, JiraHashCache, create_jira_client
from common.clients.matik_api_client import MatikApiClient, create_matik_api_client
from common.clients.sqs_publisher import SQSPublisher
from common.constants import API_V1_PREFIX
from common.datasources.registry import get_source
from common.metrics import (
    ClientMetrics,
    JiraCacheMetrics,
    JobMetrics,
    SQSPublisherMetrics,
)
from common.models.jira_batch_tracker import JiraBatchTracker
from common.models.jira_config import JiraConfig
from common.models.jira_issue import Issue
from common.models.jira_issue_record import JiraIssueRecord
from common.models.jira_issue_type import JiraIssueType
from common.models.scribe_messages import JiraBaseMessage
from common.queues.enrichment_publisher import EnrichmentPublisherSync
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive
from common.utils.github_utils import parse_pr_url
from common.utils.greenroom_utils import (
    load_jira_backstage_mapping,
    resolve_jira_services_from_mapping,
)
from historian.base.runner import HistorianContext, run_historian

logger = log_utils.get_logger(__name__)


# =============================================================================
# Batch Window Calculation
# =============================================================================


@dataclass
class BatchWindow:
    """Represents a time window for batch processing."""

    start: str
    """Batch start datetime (naive UTC)"""

    end: str
    """Batch end datetime (naive UTC)"""

    log_type: str
    """Log type: 'initial', 'lookback', 'advance', 'resume'"""


class BatchWindowError(Exception):
    """Error calculating batch window."""

    pass


def calculate_batch_window(
    tracker: JiraBatchTracker,
    lookback_days: int,
    catch_up_min_gap_minutes: int = 10,
) -> BatchWindow:
    """
    Determine the next batch time window based on tracker status.

    This is a pure function with no side effects, making it easy to test.

    Args:
        tracker: Current batch tracker state
        lookback_days: Number of days to look back when caught up
        catch_up_min_gap_minutes: Minimum gap in minutes to trigger a catch-up batch

    Returns:
        BatchWindow with start, end, and log_type

    Raises:
        BatchWindowError: If tracker has unknown status
    """
    if lookback_days <= 0:
        lookback_days = 30

    now = utc_now_naive()

    if tracker.status is None:
        # NULL status - first run, use initial batch window
        return BatchWindow(
            start=tracker.batch_start.isoformat(),
            end=tracker.batch_end.isoformat(),
            log_type="initial",
        )

    if tracker.status == "OK":
        # Previous batch succeeded
        next_batch_end = tracker.batch_end + timedelta(days=tracker.window_days)

        if next_batch_end > now:
            gap = now - tracker.batch_end
            if gap >= timedelta(minutes=catch_up_min_gap_minutes):
                # One final advance to cover the remaining window up to now,
                # before switching to lookback on the next run.
                # Meaningful gap — do one final advance to cover it
                return BatchWindow(
                    start=tracker.batch_end.isoformat(),
                    end=now.isoformat(),
                    log_type="catch_up",
                )
            # Gap is sub-10-minute (cron timing variance) — switch to lookback
            start = now - timedelta(days=lookback_days)
            end = start + timedelta(days=tracker.window_days)
            return BatchWindow(
                start=start.isoformat(),
                end=end.isoformat(),
                log_type="lookback",
            )
        else:
            # Still catching up with historical data, advance normally
            return BatchWindow(
                start=tracker.batch_end.isoformat(),
                end=next_batch_end.isoformat(),
                log_type="advance",
            )

    if tracker.status == "PROCESSING":
        # Previous run crashed mid-batch, resume same window
        return BatchWindow(
            start=tracker.batch_start.isoformat(),
            end=tracker.batch_end.isoformat(),
            log_type="resume",
        )

    raise BatchWindowError(f"unknown tracker status: {tracker.status}")


# =============================================================================
# JQL Building
# =============================================================================


class JQLBuildError(Exception):
    """Error building JQL query."""

    pass


def build_final_jql(jql: str, batch_start: str, batch_end: str) -> str:
    """
    Replace the [REPLACE BATCH DATES] placeholder with actual date conditions.

    This is a pure function with no side effects, making it easy to test.

    Args:
        jql: Base JQL with [REPLACE BATCH DATES] placeholder
        batch_start: Batch start datetime (ISO format)
        batch_end: Batch end datetime (ISO format)

    Returns:
        Final JQL with date conditions replaced

    Raises:
        JQLBuildError: If JQL is empty or missing placeholder
    """
    if not jql:
        raise JQLBuildError("base JQL is empty, cannot proceed without a base JQL")

    # Parse datetime strings and format for JQL
    # Format: "created > '2023/06/01 00:00' AND created < '2023/06/15 00:00'"
    start_dt = parse_timestamp_to_utc(batch_start)
    end_dt = parse_timestamp_to_utc(batch_end)

    if start_dt is None or end_dt is None:
        raise JQLBuildError("failed to parse batch dates")

    batch_date_conditions = (
        f"created > '{start_dt.strftime('%Y/%m/%d %H:%M')}' "
        f"AND created < '{end_dt.strftime('%Y/%m/%d %H:%M')}'"
    )

    # Replace the placeholder with actual batch date conditions
    final_jql = jql.replace("[REPLACE BATCH DATES]", batch_date_conditions)

    if final_jql == jql:
        # No replacement occurred
        raise JQLBuildError(
            "JQL does not contain [REPLACE BATCH DATES] placeholder, "
            "cannot proceed without proper JQL"
        )

    return final_jql


# =============================================================================
# Issue Conversion
# =============================================================================


def convert_issue_to_record(issue: Issue, ticket_type: str) -> JiraIssueRecord:
    """
    Convert a JIRA Issue (from Library API) to a JiraIssueRecord (our model).

    Args:
        issue: Issue model from JIRA Library API
        ticket_type: Type of ticket (tcmr, operational, etc.)

    Returns:
        JiraIssueRecord ready for database insertion
    """
    # Extract created_at - default to now if not present
    created_at = utc_now_naive()
    if issue.fields and issue.fields.created:
        parsed = parse_timestamp_to_utc(issue.fields.created)
        if parsed:
            created_at = parsed

    # Extract status name
    status_name = None
    if issue.fields and issue.fields.status:
        status_name = issue.fields.status.name

    # Extract summary
    summary = None
    if issue.fields:
        summary = issue.fields.summary

    return JiraIssueRecord(
        issue_id=issue.id or "",
        issue_key=issue.key or "",
        ticket_type=ticket_type,
        summary=summary,
        status_name=status_name,
        created_at=created_at,
        issue_summary=issue.issue_summary,
        issue_comments_summary=issue.issue_comments_summary,
        summary_hash=issue.summary_hash,
        comments_hash=issue.comments_hash,
        tcmr_related_git_pr_link=issue.tcmr_related_git_pr_link,
        tcmr_related_services=issue.tcmr_related_services,
        tcmr_planned_start_date=issue.tcmr_planned_start_date,
        tcmr_planned_end_date=issue.tcmr_planned_end_date,
    )


# =============================================================================
# API Helper Functions
# =============================================================================


def _get_batch_tracker(
    api_client: MatikApiClient, ticket_type: str
) -> JiraBatchTracker | None:
    """
    Fetch batch tracker from the Matik API.

    Args:
        api_client: Matik API client
        ticket_type: Type of ticket (tcmr, operational)

    Returns:
        JiraBatchTracker if found, None on error
    """
    try:
        response = api_client.get_request(
            f"{API_V1_PREFIX}/jira/batch/tracker/{ticket_type}"
        )
        data = json.loads(response)
        return JiraBatchTracker.model_validate(data)
    except Exception as e:
        logger.error(
            "failed to get batch tracker from API",
            ticket_type=ticket_type,
            error=str(e),
        )
        return None


def _update_batch_tracker(
    api_client: MatikApiClient, tracker: JiraBatchTracker
) -> bool:
    """
    Update batch tracker via the Matik API.

    Args:
        api_client: Matik API client
        tracker: Batch tracker to update

    Returns:
        True on success, False on failure
    """
    try:
        body = tracker.model_dump(mode="json")
        api_client.post_json_request(
            f"{API_V1_PREFIX}/jira/batch/tracker/{tracker.ticket_type}",
            body,
        )
        return True
    except Exception as e:
        logger.error(
            "failed to update batch tracker via API",
            ticket_type=tracker.ticket_type,
            error=str(e),
        )
        return False


def _enrich_with_services(
    issues: list[Issue],
    api_client: MatikApiClient,
    mapping: dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Resolve Backstage services for JIRA TCMR issues.

    Strategy:
    1. Primary: Parse tcmr_related_git_pr_link -> lookup PR services via API
    2. Fallback: Use tcmr_related_services + backstage mapping YAML

    Args:
        issues: List of JIRA issues to enrich
        api_client: Matik API client for PR service lookup
        mapping: JIRA-to-Backstage mapping from load_jira_backstage_mapping()

    Returns:
        Dict keyed by issue key -> list of resolved service names
    """
    result: dict[str, list[str]] = {}

    # Step 1: Collect all PR URLs and parse them
    pr_lookups: list[tuple[str, str, int]] = []
    issue_to_pr: dict[str, tuple[str, str, int]] = {}

    for issue in issues:
        if not issue.key:
            continue
        if issue.tcmr_related_git_pr_link:
            parsed = parse_pr_url(issue.tcmr_related_git_pr_link)
            if parsed:
                pr_lookups.append(parsed)
                issue_to_pr[issue.key] = parsed

    # Step 2: Batch lookup PR services via API
    pr_services_map: dict[str, list[str]] = {}
    if pr_lookups:
        try:
            body = [
                {"org": org, "repo": repo, "pr_number": pr_num}
                for org, repo, pr_num in pr_lookups
            ]
            response = api_client.post_json_request(
                f"{API_V1_PREFIX}/ghe/pr/services/lookup",
                body={"lookups": body},
            )
            pr_services_map = json.loads(response)
        except Exception:
            logger.exception("failed to lookup PR services via API")

    # Step 3: Assign services to issues
    for issue in issues:
        if not issue.key:
            continue

        # Try PR-based lookup first
        if issue.key in issue_to_pr:
            org, repo, pr_num = issue_to_pr[issue.key]
            pr_key = f"{org}:{repo}:{pr_num}"
            if pr_key in pr_services_map:
                services = pr_services_map[pr_key]
                if services:
                    result[issue.key] = services
                    continue

        # Fallback: Use tcmr_related_services + mapping
        if issue.tcmr_related_services and mapping:
            services = resolve_jira_services_from_mapping(
                issue.tcmr_related_services, mapping
            )
            if services:
                result[issue.key] = services

    logger.info(
        "enriched issues with services",
        total_issues=len(issues),
        enriched_count=len(result),
        pr_lookups=len(pr_lookups),
    )

    return result


def _save_issues_via_sqs(
    sqs_publisher: SQSPublisher,
    api_client: MatikApiClient,
    issues: list[Issue],
    ticket_type: str,
    backstage_mapping: dict[str, list[str]] | None = None,
) -> int:
    """
    Convert and publish JIRA issues to SQS for Scribe to write to the database.

    Args:
        sqs_publisher: SQS publisher for sending Scribe messages.
        api_client: Matik API client (used for service enrichment lookup).
        issues: List of Issue models from JIRA.
        ticket_type: Type of ticket (tcmr, operational, etc.).
        backstage_mapping: Optional JIRA-to-Backstage mapping for service enrichment.
            When None, update_services=False is sent so existing services are preserved.

    Returns:
        Number of successfully queued issues.
    """

    if not issues:
        logger.info("no issues to save", ticket_type=ticket_type)
        return 0

    # Enrich TCMR issues with resolved Backstage services
    services_map: dict[str, list[str]] = {}
    if backstage_mapping is not None:
        services_map = _enrich_with_services(issues, api_client, backstage_mapping)

    records = [convert_issue_to_record(issue, ticket_type) for issue in issues]

    # Apply enriched services to records
    for record in records:
        if record.issue_key in services_map:
            record.services = services_map[record.issue_key]

    # update_services=False when no backstage_mapping: preserve existing services in DB
    update_services = backstage_mapping is not None

    success_count = 0
    for record in records:
        try:
            message = JiraBaseMessage(
                data=record.model_dump(mode="json", exclude={"id"}),
                update_services=update_services,
                entered_at=utc_now_naive(),
            )
            sqs_publisher.send_sync(message)  # synchronous: Jira historian is not async
            success_count += 1
        except Exception as e:
            logger.error(
                "failed to queue jira issue to SQS",
                issue_key=record.issue_key,
                ticket_type=ticket_type,
                error=str(e),
            )

    logger.info(
        "queued issues to SQS",
        ticket_type=ticket_type,
        total=len(records),
        success=success_count,
        failed=len(records) - success_count,
    )

    return success_count


def _set_tracker_error(
    api_client: MatikApiClient,
    tracker: JiraBatchTracker,
    error_message: str,
) -> None:
    """Set tracker to ERROR status with error message via API."""
    tracker.status = "ERROR"
    tracker.error_message = error_message

    if not _update_batch_tracker(api_client, tracker):
        logger.error(
            "failed to update tracker to ERROR",
            ticket_type=tracker.ticket_type,
        )


def process_ticket_type(
    jira_config: JiraConfig,
    jira_client: JiraClient,
    api_client: MatikApiClient,
    ticket_type: JiraIssueType,
    jql: str,
    sqs_publisher: SQSPublisher,
    backstage_mapping: dict[str, list[str]] | None = None,
) -> bool:
    """
    Process a batch of JIRA tickets for a specific ticket type.

    This is the core processing function for the cron job. It:
    1. Fetches the batch tracker from the API to determine the time window
    2. Queries JIRA for issues in that window
    3. Enriches issues with Backstage service mapping (if mapping provided)
    4. Sends issues to SQS for Scribe to write to the database
    5. Updates the tracker status via API

    Args:
        jira_config: JIRA configuration
        jira_client: JIRA API client
        api_client: Matik API client for tracker/hash/service-enrichment calls
        ticket_type: Type of JIRA tickets to process
        jql: Base JQL query with [REPLACE BATCH DATES] placeholder
        sqs_publisher: SQS publisher for sending issues to Scribe
        backstage_mapping: Optional JIRA-to-Backstage mapping for service enrichment

    Returns:
        True on success, False on failure
    """
    type_str = ticket_type.value
    logger.info("processing ticket type", ticket_type=type_str)

    # 1. Fetch batch tracker from API
    tracker = _get_batch_tracker(api_client, type_str)
    if tracker is None:
        logger.error("failed to get batch tracker", ticket_type=type_str)
        return False

    logger.info(
        "retrieved batch tracker",
        ticket_type=type_str,
        status=tracker.status,
        batch_start=tracker.batch_start.isoformat(),
        batch_end=tracker.batch_end.isoformat(),
    )

    # 2. Handle ERROR status - requires manual intervention
    if tracker.status == "ERROR":
        logger.error(
            "tracker in ERROR state, requires manual intervention",
            ticket_type=type_str,
            error_message=tracker.error_message or "unknown error",
            batch_start=tracker.batch_start.isoformat(),
            batch_end=tracker.batch_end.isoformat(),
        )
        return False

    # 3. Calculate next batch window
    lookback_days = jira_config.lookback_days
    if lookback_days <= 0:
        lookback_days = 30

    try:
        window = calculate_batch_window(
            tracker, lookback_days, jira_config.catch_up_min_gap_minutes
        )
    except BatchWindowError as e:
        logger.error("failed to calculate batch window", error=str(e))
        return False

    # Log the batch window
    log_data = {
        "ticket_type": type_str,
        "batch_start": window.start,
        "batch_end": window.end,
    }
    if window.log_type == "initial":
        logger.info("starting initial batch", **log_data)
    elif window.log_type == "lookback":
        logger.info(
            "caught up, resetting to lookback window",
            lookback_days=lookback_days,
            **log_data,
        )
    elif window.log_type == "advance":
        logger.info("advancing to next batch", **log_data)
    elif window.log_type == "catch_up":
        logger.info("catching up remaining window to present", **log_data)
    elif window.log_type == "resume":
        logger.warning("resuming previous batch", **log_data)

    # 4. Update tracker to PROCESSING via API
    tracker.status = "PROCESSING"
    tracker.batch_start = parse_timestamp_to_utc(window.start)  # type: ignore[assignment]
    tracker.batch_end = parse_timestamp_to_utc(window.end)  # type: ignore[assignment]
    tracker.error_message = None

    if not _update_batch_tracker(api_client, tracker):
        logger.error("failed to update tracker to PROCESSING", ticket_type=type_str)
        return False

    # 5. Build final JQL
    try:
        final_jql = build_final_jql(jql, window.start, window.end)
    except JQLBuildError as e:
        logger.error("JQL build error", error=str(e), ticket_type=type_str)
        _set_tracker_error(api_client, tracker, str(e))
        return False

    logger.info("executing JQL query", jql=final_jql, ticket_type=type_str)

    # 6. Fetch issues from JIRA and publish each to the Enricher. The Enricher
    # owns LLM summarization and hash-based change detection, so no hash cache
    # is fetched here (an empty cache is passed for new-vs-existing stats only).
    try:
        raw_issues = jira_client.fetch_issues(final_jql)
    except Exception as e:
        error_msg = f"failed to fetch issues: {e}"
        logger.exception(error_msg, ticket_type=type_str)
        _set_tracker_error(api_client, tracker, error_msg)
        return False

    try:
        issues = jira_client.enrich_issues(raw_issues, JiraHashCache())
    except Exception as e:
        error_msg = f"failed to enrich issues: {e}"
        logger.exception(error_msg, ticket_type=type_str)
        _set_tracker_error(api_client, tracker, error_msg)
        return False

    logger.info("fetched issues from JIRA", count=len(issues), ticket_type=type_str)

    # 7. Send issues to SQS for Scribe to write (with service enrichment if mapping provided)
    try:
        success_count = _save_issues_via_sqs(
            sqs_publisher, api_client, issues, type_str, backstage_mapping
        )
        if success_count == 0 and len(issues) > 0:
            raise RuntimeError("all issue sends failed")
    except Exception as e:
        error_msg = f"failed to save issues: {e}"
        logger.exception(error_msg, ticket_type=type_str)
        _set_tracker_error(api_client, tracker, error_msg)
        return False

    # 8. Success - update tracker to OK via API
    tracker.status = "OK"
    tracker.error_message = None
    tracker.last_processed_at = utc_now_naive()

    if not _update_batch_tracker(api_client, tracker):
        logger.error("failed to update tracker to OK", ticket_type=type_str)
        return False

    logger.info(
        "batch completed successfully",
        ticket_type=type_str,
        batch_start=window.start,
        batch_end=window.end,
        issues_count=len(issues),
    )

    return True


# =============================================================================
# Main Entry Point
# =============================================================================


def _build_and_run(ctx: HistorianContext) -> int:
    """Build the JIRA clients + publishers and process both ticket types.

    JIRA runs two logical jobs per invocation (TCMR + OPERATIONAL), each with its
    own tracker window and job label, using a synchronous fetch/enrich/publish
    path. That two-job loop is intrinsic to JIRA, so it lives here rather than in
    the shared shell (which runs one ``build_and_run`` per invocation).
    """
    config = ctx.config

    jira_client_metrics: ClientMetrics | None = None
    cache_metrics: JiraCacheMetrics | None = None
    job_metrics: JobMetrics | None = None
    sqs_publisher_metrics: SQSPublisherMetrics | None = None
    if ctx.meter is not None and ctx.service_name is not None:
        jira_client_metrics = ClientMetrics(ctx.meter, ctx.service_name, "jira")
        cache_metrics = JiraCacheMetrics(ctx.meter, ctx.service_name)
        job_metrics = JobMetrics(ctx.meter, ctx.service_name)
        sqs_publisher_metrics = SQSPublisherMetrics(
            ctx.meter, config.telescope.service_name
        )

    # Validate required SQS configuration.
    if config.jira.sqs_queue_url is None:
        logger.error("jira.sqs_queue_url configuration is required")
        return 1

    # Enrichment publisher (SYNC — the JIRA historian is synchronous end-to-end;
    # enrichment is published inside the JIRA client's enrich_issues).
    logger.info("Creating enricher SQS publisher (sync)")
    enrichment_publisher = EnrichmentPublisherSync(
        queue_url=config.enricher.enricher_queue_url,
        region=config.enricher.region,
    )

    jira_client = create_jira_client(
        jira_config=config.jira,
        client_metrics=jira_client_metrics,
        cache_metrics=cache_metrics,
        enrichment_publisher=enrichment_publisher,
    )

    api_client = create_matik_api_client(api_config=config.api)

    # SQS publisher for issue writes via Scribe.
    sqs_session = boto3.Session(region_name=config.jira.sqs_queue_region)
    sqs_client = sqs_session.client("sqs")
    sqs_publisher = SQSPublisher(
        sqs_client, config.jira.sqs_queue_url, sqs_publisher_metrics
    )

    # Note: This is temporary while we are still waiting for greenroom access to be approved
    # This will be changed to downloading from artifactory
    # Load JIRA-to-Backstage mapping for service enrichment
    backstage_mapping = load_jira_backstage_mapping(
        "/app/config/jira-backstage-mapping.yml"
    )

    # Process ticket types with job metrics.
    success = True

    if config.jira.tcmr_jql_enabled and config.jira.tcmr_jql:
        logger.info("processing TCMR tickets")
        record_job = job_metrics.start_job("jira_tcmr") if job_metrics else None
        try:
            if not process_ticket_type(
                config.jira,
                jira_client,
                api_client,
                JiraIssueType.TCMR,
                config.jira.tcmr_jql,
                sqs_publisher=sqs_publisher,
                backstage_mapping=backstage_mapping,
            ):
                logger.error("TCMR processing failed")
                success = False
                if record_job:
                    record_job(0, Exception("TCMR processing failed"))
            else:
                if record_job:
                    record_job(1, None)  # 1 batch processed successfully
        except Exception as e:
            logger.exception("TCMR processing error")
            success = False
            if record_job:
                record_job(0, e)
    else:
        logger.info("tcmr_jql not configured, skipping")

    if config.jira.operational_jql_enabled and config.jira.operational_jql:
        logger.info("processing operational tickets")
        record_job = job_metrics.start_job("jira_operational") if job_metrics else None
        try:
            if not process_ticket_type(
                config.jira,
                jira_client,
                api_client,
                JiraIssueType.OPERATIONAL,
                config.jira.operational_jql,
                sqs_publisher=sqs_publisher,
            ):
                logger.error("operational processing failed")
                success = False
                if record_job:
                    record_job(0, Exception("Operational processing failed"))
            else:
                if record_job:
                    record_job(1, None)  # 1 batch processed successfully
        except Exception as e:
            logger.exception("Operational processing error")
            success = False
            if record_job:
                record_job(0, e)
    else:
        logger.info("operational_jql not configured, skipping")

    if success:
        logger.info("jira historian completed successfully")
        return 0
    logger.error("jira historian completed with errors")
    return 1


def main() -> int:
    """Run the Jira historian cron job.

    This is a K8s CronJob entry point. It processes configured JIRA ticket types
    (TCMR, operational) and exits with appropriate status codes.

    Returns:
        0 on success, 1 on failure.
    """
    return run_historian(
        spec=get_source("jira"),
        caller_name=__name__,
        source_config_files=[
            ("matik-historian-jira-config.yml", "Failed to load config"),
            ("metrics.yml", "Failed to load metrics config"),
            ("matik-enricher-config.yml", "Failed to load enricher config"),
        ],
        build_and_run=_build_and_run,
        source_config_missing_msg="jira configuration is missing",
        crash_log_msg="jira historian failed with unexpected error",
    )


if __name__ == "__main__":  # pragma: no cover - entry point guard
    import sys

    sys.exit(main())
