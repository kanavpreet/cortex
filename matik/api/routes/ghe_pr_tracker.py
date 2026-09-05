"""GitHub Enterprise PR tracker endpoints."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routes.deps import GHEAPIMetricsDep, GHEPRTrackerDAODep
from common.constants import API_V1_PREFIX
from common.models import GHEPRTracker
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/ghe", tags=["ghe-pr-tracker"])


class GetTrackerCutoffResponse(BaseModel):
    """Response for getting tracker cutoff."""

    org_id: int
    repo_id: int
    cutoff_date: datetime | None
    exists: bool


class UpdateTrackerResponse(BaseModel):
    """Response for updating tracker."""

    success: bool
    message: str
    org_id: int
    repo_id: int


@router.get("/tracker/{org_id}/{repo_id}", status_code=status.HTTP_200_OK)
def get_tracker_cutoff(
    org_id: int,
    repo_id: int,
    dao: GHEPRTrackerDAODep,
    metrics: GHEAPIMetricsDep,
) -> GetTrackerCutoffResponse:
    """
    Get cutoff date for a specific org/repo tracker.

    Used to determine where to resume incremental crawling.
    """
    record = metrics.start_operation("get", "tracker") if metrics else None

    try:
        cutoff = dao.get_tracker_cutoff(org_id, repo_id)

        if record:
            record(True)
        return GetTrackerCutoffResponse(
            org_id=org_id,
            repo_id=repo_id,
            cutoff_date=cutoff,
            exists=cutoff is not None,
        )
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise


@router.post("/tracker", status_code=status.HTTP_200_OK)
def update_tracker(
    tracker: GHEPRTracker,
    dao: GHEPRTrackerDAODep,
    metrics: GHEAPIMetricsDep,
) -> UpdateTrackerResponse:
    """
    Upsert tracker information for org/repo.

    Creates tracker if not exists, updates if exists.
    """
    # Validate required fields
    if tracker.org_id == 0 or tracker.repo_id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_id and repo_id are required",
        )

    record = metrics.start_operation("upsert", "tracker") if metrics else None

    try:
        success = dao.upsert_tracker(tracker)
        if not success:
            if record:
                try:
                    record(False)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.error(
                f"Failed to upsert tracker for org={tracker.org_id}, "
                f"repo={tracker.repo_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upsert tracker",
            )

        if record:
            record(True)
        logger.info(
            f"Upserted tracker for org={tracker.org_id}, repo={tracker.repo_id}"
        )
        return UpdateTrackerResponse(
            success=True,
            message="Tracker updated successfully",
            org_id=tracker.org_id,
            repo_id=tracker.repo_id,
        )
    except HTTPException:
        raise
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise


@router.get("/trackers", status_code=status.HTTP_200_OK)
def get_all_trackers(
    dao: GHEPRTrackerDAODep,
    metrics: GHEAPIMetricsDep,
) -> list[GHEPRTracker]:
    """
    Get all tracker records.

    Used for monitoring crawling progress across all repositories.
    """
    record = metrics.start_operation("get_all", "tracker") if metrics else None

    try:
        trackers = dao.get_all_trackers()
        if trackers is None:
            if record:
                try:
                    record(False)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.error("Failed to get all trackers")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get all trackers",
            )

        if record:
            record(True)
        return trackers
    except HTTPException:
        raise
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise
