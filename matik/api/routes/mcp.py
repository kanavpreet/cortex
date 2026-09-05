"""MCP-facing source data endpoints.

All endpoints live under /v1/mcp/ so they are easily discoverable as MCP
tools. The expected call flow is:

1. Fetch correlation group:   GET  /v1/mcp/correlation/reliability/group/{anchor_entity_id}
2. Fetch correlation events:  GET  /v1/mcp/correlation/reliability/events/{anchor_entity_id}
3. Fetch source records by the entity_ids returned in step 2:
     GET /v1/mcp/incidentio/incidents?reference_ids=INC-1&reference_ids=INC-2
     GET /v1/mcp/jira/issues?issue_keys=TCMR-1&issue_keys=TCMR-2
     GET /v1/mcp/ghe/prs?pull_request_ids=101&pull_request_ids=102

Descriptions on each route are intentionally verbose because they are
surfaced directly to the LLM as the tool description in the MCP protocol.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.routes.deps import (
    GHEPRDAODep,
    IncidentIOIncidentDAODep,
    JiraIssuesDAODep,
    ReliabilityCorrelationDAODep,
    ReliabilityCorrelationGroupDAODep,
)
from common.constants import GHE_BASE_URL, INCIDENTIO_BASE_URL, JIRA_BROWSE_URL
from common.models.ghe_pr import GHEPullRequest
from common.models.incidentio_incident import IncidentIOIncident
from common.models.jira_issue_record import JiraIssueRecord
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.reliability_correlation_group import ReliabilityCorrelationGroup
from common.utils import log_utils, make_response_model
from common.utils.list_utils import deduplicate_by_priority

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


# These are dynamically created Pydantic models. They are annotated as
# type[BaseModel] so mypy understands the variable type; the # type: ignore
# comments on return annotations below acknowledge that dynamically-created
# classes cannot be used as static generic parameters.
IncidentIOIncidentMCPResponse: type[BaseModel] = make_response_model(
    IncidentIOIncident,
    "IncidentIOIncidentMCPResponse",
    url=(str, Field(..., description="Direct URL to the incident in Incident.io")),
)

JiraIssueRecordMCPResponse: type[BaseModel] = make_response_model(
    JiraIssueRecord,
    "JiraIssueRecordMCPResponse",
    url=(str, Field(..., description="Direct URL to the JIRA issue")),
)

GHEPullRequestMCPResponse: type[BaseModel] = make_response_model(
    GHEPullRequest,
    "GHEPullRequestMCPResponse",
    org=(str, Field(..., description="GitHub organization name (e.g., 'Airbnb')")),
    repo_name=(
        str,
        Field(..., description="GitHub repository name (e.g., 'my-service')"),
    ),
    url=(
        str,
        Field(..., description="Direct URL to the pull request in GitHub Enterprise"),
    ),
)


def _normalize_incident_reference_id(ref: str) -> str:
    """Normalize an incident reference ID to INC-XXXX format.

    Accepts plain numbers (e.g. '5267') or full references with any casing
    (e.g. 'inc-5267', 'INC-5267') and returns the canonical 'INC-XXXX' form.
    """
    ref = ref.strip().upper()
    if ref.isdigit():
        return f"INC-{ref}"
    return ref


# ---------------------------------------------------------------------------
# Correlation endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/correlation/reliability/group/{anchor_entity_id}",
    status_code=status.HTTP_200_OK,
    operation_id="mcp_get_correlation_group",
    summary="Get reliability correlation group for an incident",
    description=(
        "STEP 1 of the reliability correlation workflow. Call this endpoint first "
        "when investigating an incident to retrieve the aggregated correlation group metadata.\n\n"
        "Path parameter:\n"
        "- anchor_entity_id (string, required): The Incident.io reference ID used as the "
        "anchor for correlation (e.g., 'INC-99', 'INC-1234'). This is the human-readable "
        "reference ID displayed in the Incident.io product.\n\n"
        "Returns a ReliabilityCorrelationGroup object with:\n"
        "- anchor_entity_id: The incident reference ID passed in\n"
        "- anchor_type: Category of the anchor entity (e.g., 'incident')\n"
        "- correlation_timestamp: ISO 8601 UTC timestamp of when the correlation was computed\n"
        "- feedback_state: Human feedback on correlation quality ('NEUTRAL', 'POSITIVE', 'NEGATIVE')\n"
        "- review_status: Whether a human has reviewed this correlation (boolean)\n\n"
        "Returns null if no correlation group has been computed for the given anchor entity.\n\n"
        "Next step: call GET /v1/mcp/correlation/reliability/events/{anchor_entity_id} "
        "with the same anchor_entity_id to retrieve the individual correlated entities."
    ),
)
def get_correlation_group(
    anchor_entity_id: str,
    group_dao: ReliabilityCorrelationGroupDAODep,
) -> ReliabilityCorrelationGroup | None:
    """Get the reliability correlation group for an anchor entity."""
    return group_dao.find_group(anchor_entity_id)


@router.get(
    "/correlation/reliability/events/{anchor_entity_id}",
    status_code=status.HTTP_200_OK,
    operation_id="mcp_get_correlation_events",
    summary="Get individual correlation events for an incident",
    description=(
        "STEP 2 of the reliability correlation workflow. Call this endpoint after "
        "retrieving the correlation group to get the list of individual entities "
        "(pull requests, JIRA tickets) that were correlated with the anchor incident.\n\n"
        "Path parameter:\n"
        "- anchor_entity_id (string, required): The Incident.io reference ID "
        "(e.g., 'INC-99', 'INC-1234'). Must match the anchor_entity_id used in step 1.\n\n"
        "Response format: returns a JSON object with a top-level 'events' key containing "
        "a list: {\"events\": [...]}. Access results via response['events'].\n\n"
        "Each ReliabilityCorrelation object contains:\n"
        "- anchor_entity_id: The anchor incident reference ID\n"
        "- entity_type: Type of the correlated entity — 'github_pr' for GitHub pull "
        "requests, 'jira_tcmr' for JIRA TCMR tickets\n"
        "- entity_id: Identifier of the correlated entity. For 'github_pr': the numeric "
        "pull request ID as a string (e.g., '12345'). For 'jira_tcmr': the JIRA issue "
        "key (e.g., 'TCMR-100')\n"
        "- correlation_type: How the correlation was determined (e.g., 'SERVICE_MATCH', 'LLM')\n"
        "- final_score: Confidence score (0.0-1.0) after applying the path-specific cap. "
        "Use this as the primary relevance signal: scores above 0.7 indicate strong "
        "correlation, 0.4-0.7 moderate, below 0.4 weak. Sort or filter results by "
        "final_score descending to surface the most likely contributing events first. "
        "May be null if scoring has not yet been computed.\n"
        "- base_score: Raw confidence score before the path-specific cap is applied "
        "(time-based or LLM-provided). Useful for understanding the original signal "
        "strength before normalization.\n"
        "- scoring_version: Identifier of the scoring algorithm used (e.g., 'incident_v1'). "
        "Use this to understand how the score was produced.\n"
        "- reasoning: Human-readable explanation of why this correlation was made. "
        "Present this text to explain why a correlated event is relevant to the incident.\n"
        "- services: List of Backstage service identifiers associated with the correlated "
        "entity. Useful for understanding which services are involved.\n"
        "- start_time: ISO 8601 UTC timestamp for when the correlated event started.\n"
        "- end_time: ISO 8601 UTC timestamp for when the correlated event ended.\n\n"
        "Returns {'events': []} if no correlation events exist for the given anchor.\n\n"
        "Next steps based on entity_type values returned:\n"
        "- For 'github_pr' entities: collect unique entity_id values, convert each to integer, "
        "and call GET /v1/mcp/ghe/prs?pull_request_ids=<id1>&pull_request_ids=<id2>\n"
        "- For 'jira_tcmr' entities: collect unique entity_id values and call "
        "GET /v1/mcp/jira/issues?issue_keys=<key1>&issue_keys=<key2>\n"
        "- To fetch the anchor incident's full details: call "
        "GET /v1/mcp/incidentio/incidents?reference_ids=<anchor_entity_id>"
    ),
)
def get_correlation_events(
    anchor_entity_id: str,
    correlation_dao: ReliabilityCorrelationDAODep,
) -> dict[str, list[ReliabilityCorrelation]]:
    """Get all correlation events for an anchor entity."""
    correlations = correlation_dao.find_by_anchor(anchor_entity_id)
    return {
        "events": deduplicate_by_priority(
            correlations,
            key_field="entity_id",
            priority_field="correlation_type",
            priority=["SERVICE_MATCH", "LLM"],
        )
    }


# ---------------------------------------------------------------------------
# Source record endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/incidentio/incidents",
    status_code=status.HTTP_200_OK,
    operation_id="mcp_get_incidentio_incidents",
    summary="Get Incident.io incidents by reference IDs",
    description=(
        "STEP 3c of the reliability correlation workflow. Fetch full Incident.io incident "
        "records for one or more incident reference IDs.\n\n"
        "WHERE TO GET THE IDs: Pass the anchor_entity_id value (e.g., 'INC-5267') directly "
        "as reference_ids. The anchor_entity_id from the correlation group IS the incident "
        "reference ID — do NOT use entity_id values from correlation events for this endpoint "
        "(those are for JIRA issues and GitHub PRs, not for the anchor incident itself).\n\n"
        "Query parameters:\n"
        "- reference_ids (list of strings, required): One or more Incident.io reference IDs "
        "to look up (e.g., 'INC-99', 'INC-1234'). Repeat the parameter for multiple "
        "values: reference_ids=INC-1&reference_ids=INC-2. Returns HTTP 400 if not provided.\n\n"
        "Response format: returns a JSON object with a top-level 'incidents' key containing "
        "a list: {\"incidents\": [...]}. Access results via response['incidents'].\n\n"
        "Each IncidentIOIncident object contains:\n"
        "- incident_id: Internal Incident.io UUID\n"
        "- reference_id: Human-readable reference ID (e.g., 'INC-99')\n"
        "- url: Direct link to the incident in Incident.io (e.g., 'https://app.incident.io/airbnb/incidents/99'). "
        "Present this to the user so they can navigate directly to the incident.\n"
        "- severity: Incident severity (e.g., 'Sev-1', 'Sev-2')\n"
        "- status: Current incident status (e.g., 'open', 'closed')\n"
        "- visibility: Incident visibility ('public' or 'private')\n"
        "- affected_services: List of service names impacted by the incident\n"
        "- created_at, reported_at: ISO 8601 UTC timestamps\n"
        "- description_summary: LLM-generated summary of the incident description\n"
        "- root_cause_summary: LLM-generated root cause analysis\n"
        "- incident_channel_summary: LLM-generated summary of the incident's "
        "Slack channel activity (problem/causes, impact, investigation status), "
        "sourced from OpsBot's on-demand channel feed. May be null if OpsBot has "
        "not yet posted a summary for this incident."
    ),
)
def get_incidents_by_reference_ids(
    incident_dao: IncidentIOIncidentDAODep,
    reference_ids: Annotated[
        list[str] | None, Query(description="Incident reference IDs")
    ] = None,
) -> dict[str, list[IncidentIOIncidentMCPResponse]]:  # type: ignore[valid-type]
    """Get Incident.io incidents by a list of reference IDs."""
    if not reference_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "reference_ids is required. "
                "Pass the anchor_entity_id from the correlation group as reference_ids "
                "(e.g., reference_ids=INC-5267)."
            ),
        )
    normalized = [_normalize_incident_reference_id(r) for r in reference_ids]
    incidents = incident_dao.find_incidents_by_reference_ids(normalized)
    return {
        "incidents": [
            IncidentIOIncidentMCPResponse(
                **incident.model_dump(),
                url=f"{INCIDENTIO_BASE_URL}/{incident.reference_id.split('-')[-1]}",
            )
            for incident in incidents
        ]
    }


@router.get(
    "/jira/issues",
    status_code=status.HTTP_200_OK,
    operation_id="mcp_get_jira_issues",
    summary="Get JIRA issues by issue keys",
    description=(
        "STEP 3a of the reliability correlation workflow. Fetch full JIRA issue records "
        "for correlated TCMR (Technical Change Management Review) tickets.\n\n"
        "WHERE TO GET THE IDs: From the correlation events list (step 2), filter for items "
        "where entity_type == 'jira_tcmr', then collect their entity_id values "
        "(e.g., 'TCMR-123'). Pass those directly as issue_keys.\n\n"
        "Query parameters:\n"
        "- issue_keys (list of strings, required): One or more JIRA issue keys to look up "
        "(e.g., 'TCMR-123', 'TCMR-456'). Repeat the parameter for multiple values: "
        "issue_keys=TCMR-1&issue_keys=TCMR-2. Returns HTTP 400 if not provided.\n\n"
        "Response format: returns a JSON object with a top-level 'issues' key containing "
        "a list: {\"issues\": [...]}. Access results via response['issues'].\n\n"
        "Each JiraIssueRecord object contains:\n"
        "- issue_id: Internal JIRA issue ID\n"
        "- issue_key: JIRA issue key (e.g., 'TCMR-123')\n"
        "- ticket_type: Type of ticket (e.g., 'tcmr', 'operational', 'alert')\n"
        "- summary: Issue title/summary text\n"
        "- status_name: Current issue status (e.g., 'Open', 'In Progress', 'Done')\n"
        "- created_at: ISO 8601 UTC timestamp of when the issue was created\n"
        "- tcmr_planned_start_date, tcmr_planned_end_date: Change window dates for TCMR tickets\n"
        "- services: List of Backstage service identifiers associated with the change\n"
        "- url: Direct link to the JIRA issue (e.g., 'https://jira.airbnb.biz/browse/TCMR-123'). "
        "Present this to the user so they can navigate directly to the issue.\n"
        "- issue_summary: LLM-generated summary of the issue\n"
        "- issue_comments_summary: LLM-generated summary of the issue's comments"
    ),
)
def get_jira_issues_by_keys(
    jira_dao: JiraIssuesDAODep,
    issue_keys: Annotated[
        list[str] | None, Query(description="JIRA issue keys")
    ] = None,
) -> dict[str, list[JiraIssueRecordMCPResponse]]:  # type: ignore[valid-type]
    """Get JIRA issues by a list of issue keys."""
    if not issue_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "issue_keys is required. "
                "From the correlation events (step 2), collect entity_id values where "
                "entity_type == 'jira_tcmr' and pass them as issue_keys "
                "(e.g., issue_keys=TCMR-123)."
            ),
        )
    issues = jira_dao.find_issues_by_keys(issue_keys)
    return {
        "issues": [
            JiraIssueRecordMCPResponse(
                **issue.model_dump(),
                url=f"{JIRA_BROWSE_URL}/{issue.issue_key}",
            )
            for issue in issues
        ]
    }


@router.get(
    "/ghe/prs",
    status_code=status.HTTP_200_OK,
    operation_id="mcp_get_ghe_prs",
    summary="Get GHE pull requests by pull request IDs",
    description=(
        "STEP 3b of the reliability correlation workflow. Fetch full GitHub Enterprise "
        "pull request records for correlated code changes.\n\n"
        "WHERE TO GET THE IDs: From the correlation events list (step 2), filter for items "
        "where entity_type == 'github_pr', then collect their entity_id values. "
        "Each entity_id is the GitHub internal PR ID stored as a string (e.g., '12345678'). "
        "You MUST convert each entity_id from string to integer before passing as "
        "pull_request_ids — passing strings will cause a validation error.\n\n"
        "Query parameters:\n"
        "- pull_request_ids (list of integers, required to be integers): One or more GitHub "
        "internal PR IDs. Repeat the parameter for multiple values: "
        "pull_request_ids=101&pull_request_ids=102. Returns HTTP 400 if not provided.\n\n"
        "Response format: returns a JSON object with a top-level 'pull_requests' key "
        'containing a list: {"pull_requests": [...]}. Access results via '
        "response['pull_requests'].\n\n"
        "Each GHEPullRequest object contains:\n"
        "- pull_request_id: GitHub internal PR ID (matches the entity_id from correlation events)\n"
        "- pull_request_number: PR number shown in GitHub URLs (e.g., /pull/42)\n"
        "- org: GitHub organization name (e.g., 'Airbnb')\n"
        "- repo_name: GitHub repository name (e.g., 'my-service')\n"
        "- title: Pull request title\n"
        "- state: PR state ('open' or 'closed')\n"
        "- merged: Whether the PR was merged (boolean)\n"
        "- created_at: ISO 8601 UTC timestamp of when the PR was created\n"
        "- merged_at: ISO 8601 UTC timestamp of when the PR was merged, or null\n"
        "- repo_id: GitHub ID of the repository the PR belongs to\n"
        "- target_branch_name: The branch the PR was merged into\n"
        "- url: Direct link to the pull request in GitHub Enterprise "
        "(e.g., 'https://github.airbnb.biz/Airbnb/my-repo/pull/42'). "
        "Present this to the user so they can navigate directly to the PR.\n"
        "- pull_request_summary: LLM-generated summary of the PR description\n"
        "- services: List of Backstage service identifiers associated with this PR"
    ),
)
def get_prs_by_ids(
    pr_dao: GHEPRDAODep,
    pull_request_ids: Annotated[
        list[int] | None, Query(description="GitHub pull request IDs")
    ] = None,
) -> dict[str, list[GHEPullRequestMCPResponse]]:  # type: ignore[valid-type]
    """Get GHE pull requests by a list of pull_request_ids."""
    if not pull_request_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "pull_request_ids is required. "
                "From the correlation events (step 2), collect entity_id values where "
                "entity_type == 'github_pr', convert each from string to integer, and "
                "pass them as pull_request_ids (e.g., pull_request_ids=12345678)."
            ),
        )
    prs_with_repo = pr_dao.find_prs_with_repo_by_pull_request_ids(pull_request_ids)
    return {
        "pull_requests": [
            # repo_name is already a field on the PR; org and url are added.
            # Use model_validate on a merged dict to avoid duplicate kwargs.
            GHEPullRequestMCPResponse.model_validate(
                {
                    **item.pr.model_dump(),
                    "org": item.org,
                    "url": (
                        f"{GHE_BASE_URL}/{item.org}/{item.repo_name}"
                        f"/pull/{item.pr.pull_request_number}"
                    ),
                }
            )
            for item in prs_with_repo
        ]
    }
