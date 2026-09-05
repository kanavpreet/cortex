"""JIRA issue and batch tracker endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel

from api.routes.deps import JiraBatchTrackerDAODep, JiraIssuesDAODep
from common.constants import API_V1_PREFIX
from common.models import JiraBatchTracker, JiraIssueRecord
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/jira", tags=["jira"])

# Valid ticket types
VALID_TICKET_TYPES = {"tcmr", "operational", "alert"}


class GetHashesRequest(BaseModel):
    """Request body for fetching JIRA issue hashes."""

    issue_keys: list[str]


class JiraHashInfoResponse(BaseModel):
    """Hash and summary data for a single JIRA issue."""

    issue_key: str
    summary_hash: str | None
    comments_hash: str | None
    issue_summary: str | None
    issue_comments_summary: str | None


class UpdateBatchTrackerResponse(BaseModel):
    """Response for updating batch tracker."""

    message: str
    ticket_type: str
    status: str | None


def validate_ticket_type(ticket_type: str) -> None:
    """Validate ticket type is one of the allowed values."""
    if ticket_type not in VALID_TICKET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ticket_type must be one of: tcmr, operational, alert",
        )


@router.post("/issues/hashes", status_code=status.HTTP_200_OK)
def get_jira_issue_hashes(
    request: GetHashesRequest,
    dao: JiraIssuesDAODep = None,  # type: ignore[assignment]
) -> list[JiraHashInfoResponse]:
    """
    Get hashes and cached summaries for a list of JIRA issue keys.

    Used by the historian to check if LLM summaries need regeneration.
    When hash matches, the existing summaries are preserved.
    """
    if not request.issue_keys:
        return []

    hash_map = dao.get_issue_hashes_by_keys(request.issue_keys)
    if hash_map is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch issue hashes",
        )

    return [
        JiraHashInfoResponse(
            issue_key=info.issue_key,
            summary_hash=info.summary_hash,
            comments_hash=info.comments_hash,
            issue_summary=info.issue_summary,
            issue_comments_summary=info.issue_comments_summary,
        )
        for info in hash_map.values()
    ]


@router.get("/issues", status_code=status.HTTP_200_OK)
def get_jira_issues(
    start_time: Annotated[datetime, Query(description="Start of time range (UTC)")],
    end_time: Annotated[datetime, Query(description="End of time range (UTC)")],
    services: Annotated[
        str | None, Query(description="Comma-separated service names to filter by")
    ] = None,
    time_field: Annotated[
        str, Query(description="Timestamp column to filter on")
    ] = "created_at",
    dao: JiraIssuesDAODep = None,  # type: ignore[assignment]
) -> list[JiraIssueRecord]:
    """
    Get JIRA issues within a time range, optionally filtered by services.
    """
    start_utc = parse_timestamp_to_utc(start_time)
    end_utc = parse_timestamp_to_utc(end_time)
    assert start_utc is not None  # guaranteed: start_time is required
    assert end_utc is not None  # guaranteed: end_time is required
    services_list = (
        [s.strip() for s in services.split(",") if s.strip()] if services else None
    )
    try:
        issues = dao.find_issues_by_time_range(
            start_utc, end_utc, services_list, time_field
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from None
    if issues is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query JIRA issues",
        )

    logger.info(
        "retrieved JIRA issues",
        total=len(issues),
        start=str(start_utc),
        end=str(end_utc),
        services=services,
        time_field=time_field,
    )
    return issues


@router.get("/batch/tracker/{ticket_type}", status_code=status.HTTP_200_OK)
def get_batch_tracker(
    ticket_type: str = Path(
        ..., description="Ticket type: tcmr, operational, or alert"
    ),
    dao: JiraBatchTrackerDAODep = None,  # type: ignore[assignment]
) -> JiraBatchTracker:
    """
    Get batch tracker for a specific ticket type.

    Returns the batch processing status and window for the ticket type.
    """
    validate_ticket_type(ticket_type)
    logger.debug(f"Fetching batch tracker for ticket type: {ticket_type}")

    tracker = dao.get_tracker_by_type(ticket_type)
    logger.debug(f"Batch tracker fetched: {tracker}")
    if tracker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="batch tracker not found",
        )

    return tracker


@router.post("/batch/tracker/{ticket_type}", status_code=status.HTTP_200_OK)
def post_batch_tracker(
    tracker: JiraBatchTracker,
    ticket_type: str = Path(
        ..., description="Ticket type: tcmr, operational, or alert"
    ),
    dao: JiraBatchTrackerDAODep = None,  # type: ignore[assignment]
) -> UpdateBatchTrackerResponse:
    """
    Update batch tracker for a specific ticket type.

    Updates the batch processing status and window.
    """
    validate_ticket_type(ticket_type)

    # Ensure ticket_type matches URL parameter
    if tracker.ticket_type != ticket_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ticket_type in URL must match ticket_type in body",
        )

    success = dao.update_tracker(tracker)
    if not success:
        logger.error(f"Failed to update batch tracker for {ticket_type}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update batch tracker",
        )

    logger.info(f"Updated batch tracker for {ticket_type} (status={tracker.status})")
    return UpdateBatchTrackerResponse(
        message="batch tracker updated successfully",
        ticket_type=ticket_type,
        status=tracker.status,
    )
