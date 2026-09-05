"""Incident.io incident endpoints."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routes.deps import (
    IncidentIOIncidentDAODep,
    IncidentIOTrackerDAODep,
)
from common.constants import API_V1_PREFIX
from common.models import IncidentIOIncident, IncidentIOTracker
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/incidentio", tags=["incidentio"])


class GetHashesRequest(BaseModel):
    """Request body for fetching incident hashes."""

    incident_ids: list[str]


@router.get("/incident/tracker/lastrecorded", status_code=status.HTTP_200_OK)
def get_last_recorded(
    tracker_dao: IncidentIOTrackerDAODep,
) -> IncidentIOTracker | None:
    """
    Get the last recorded incident tracker.

    Returns the tracker with cursor position for pagination.
    """
    tracker = tracker_dao.find_last_recorded()
    if tracker is None:
        logger.warning("No tracker found in database")
    return tracker


@router.post("/incident/tracker/lastrecorded", status_code=status.HTTP_200_OK)
def post_last_recorded(
    tracker: IncidentIOTracker,
    tracker_dao: IncidentIOTrackerDAODep,
) -> dict[str, str]:
    """
    Update the last recorded incident tracker.

    Updates the cursor position after crawling.
    """
    success = tracker_dao.update_tracker(tracker)
    if not success:
        logger.error("Failed to update tracker")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update tracker",
        )

    logger.info(
        f"Updated tracker: status={tracker.status}, initial_sync_complete={tracker.initial_sync_complete}"
    )
    return {"message": "tracker updated successfully"}


@router.get("/incident/{reference_id}", status_code=status.HTTP_200_OK)
def get_incident(
    reference_id: str,
    incident_dao: IncidentIOIncidentDAODep,
) -> IncidentIOIncident:
    """
    Get an Incident.io incident by reference ID.

    Args:
        reference_id: Reference ID displayed across product (e.g., INC-1234)
    """
    incident = incident_dao.find_incident(reference_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident not found: {reference_id}",
        )

    return incident


@router.post("/incident/hashes", status_code=status.HTTP_200_OK)
def get_incident_llm_data(
    request: GetHashesRequest,
    incident_dao: IncidentIOIncidentDAODep,
) -> dict[str, dict[str, str | None]]:
    """
    Get LLM data (hashes and summaries) for a list of incident IDs.

    Used by crawler to check if LLM summaries need regeneration.
    When hash matches, the existing summaries are preserved.

    Returns a map of incident_id -> {root_cause_summary_hash, root_cause_summary, description_summary, description_hash}.
    """
    if not request.incident_ids:
        return {}

    return incident_dao.get_llm_data_by_incident_ids(request.incident_ids)
