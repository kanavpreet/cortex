"""GitHub Enterprise per-org crawl tracker endpoints.

Stores the start time of the last fully-successful crawl per organization so
the historian can skip repositories that have not been pushed to since then.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routes.deps import GHEAPIMetricsDep, GHEOrgCrawlTrackerDAODep
from common.constants import API_V1_PREFIX
from common.models import GHEOrgCrawlTracker
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/ghe", tags=["ghe-org-crawl-tracker"])


class GetOrgWatermarkResponse(BaseModel):
    """Response for getting an org's last-crawled watermark."""

    org_id: int
    last_crawled_at: datetime | None
    exists: bool


class UpdateOrgWatermarkResponse(BaseModel):
    """Response for updating an org's watermark."""

    success: bool
    message: str
    org_id: int


@router.get("/org-crawl-tracker/{org_id}", status_code=status.HTTP_200_OK)
def get_org_watermark(
    org_id: int,
    dao: GHEOrgCrawlTrackerDAODep,
    metrics: GHEAPIMetricsDep,
) -> GetOrgWatermarkResponse:
    """
    Get the last successful crawl start time for an organization.

    Used to skip repositories that have not been pushed to since the last run.
    """
    record = metrics.start_operation("get", "org_crawl_tracker") if metrics else None

    try:
        last_crawled_at = dao.get_last_crawled_at(org_id)

        if record:
            record(True)
        return GetOrgWatermarkResponse(
            org_id=org_id,
            last_crawled_at=last_crawled_at,
            exists=last_crawled_at is not None,
        )
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise


@router.post("/org-crawl-tracker", status_code=status.HTTP_200_OK)
def update_org_watermark(
    tracker: GHEOrgCrawlTracker,
    dao: GHEOrgCrawlTrackerDAODep,
    metrics: GHEAPIMetricsDep,
) -> UpdateOrgWatermarkResponse:
    """
    Upsert the per-org crawl watermark.

    Creates the record if it does not exist, updates it otherwise.
    """
    if tracker.org_id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_id is required",
        )

    record = metrics.start_operation("upsert", "org_crawl_tracker") if metrics else None

    try:
        success = dao.upsert_watermark(tracker)
        if not success:
            if record:
                try:
                    record(False)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.error(f"Failed to upsert org crawl tracker for org={tracker.org_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upsert org crawl tracker",
            )

        if record:
            record(True)
        logger.info(f"Upserted org crawl tracker for org={tracker.org_id}")
        return UpdateOrgWatermarkResponse(
            success=True,
            message="Org crawl tracker updated successfully",
            org_id=tracker.org_id,
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
