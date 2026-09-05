"""GitHub Enterprise pull request endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from api.routes.deps import GHEAPIMetricsDep, GHEPRDAODep
from common.constants import API_V1_PREFIX
from common.models import GHEPullRequest
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/ghe", tags=["ghe-pr"])


class PRLookupItem(BaseModel):
    """Single PR identifier for service lookup."""

    org: str
    repo: str
    pr_number: int


class PRServicesLookupRequest(BaseModel):
    """Request body for batch PR service lookup."""

    lookups: list[PRLookupItem]


class PRHashInfoResponse(BaseModel):
    """Response for PR hash info."""

    pull_request_id: int
    repository_id: int
    description_hash: str | None
    pull_request_summary: str | None
    services: list[str] | None = None


@router.post("/pr/services/lookup", status_code=status.HTTP_200_OK)
def lookup_pr_services(
    request: PRServicesLookupRequest,
    pr_dao: GHEPRDAODep,
    metrics: GHEAPIMetricsDep,
) -> dict[str, list[str]]:
    """
    Batch lookup of Backstage services for PRs by org/repo/pr_number.

    Returns a dict keyed by "org:repo:pr_number" with service lists as values.
    """
    if not request.lookups:
        return {}

    record = (
        metrics.start_operation("services_lookup", "pull_request") if metrics else None
    )

    try:
        tuples = [(item.org, item.repo, item.pr_number) for item in request.lookups]
        result_map = pr_dao.get_services_by_pr_identifiers(tuples)

        # Convert tuple keys to string keys for JSON response
        response: dict[str, list[str]] = {}
        for (org, repo, pr_num), services in result_map.items():
            key = f"{org}:{repo}:{pr_num}"
            response[key] = services

        if record:
            record(True)

        logger.info(
            f"PR services lookup: {len(request.lookups)} requested, "
            f"{len(response)} found"
        )
        return response
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise


@router.get("/pr", status_code=status.HTTP_200_OK)
def get_ghe_pr(
    start_time: Annotated[datetime, Query(description="Start of time range (UTC)")],
    end_time: Annotated[datetime, Query(description="End of time range (UTC)")],
    services: Annotated[
        str | None, Query(description="Comma-separated service names to filter by")
    ] = None,
    time_field: Annotated[
        str, Query(description="Timestamp column to filter on")
    ] = "created_at",
    pr_dao: GHEPRDAODep = None,  # type: ignore[assignment]
) -> list[GHEPullRequest]:
    """
    Get GHE pull requests within a time range, optionally filtered by services.
    """
    start_utc = parse_timestamp_to_utc(start_time)
    end_utc = parse_timestamp_to_utc(end_time)
    assert start_utc is not None  # guaranteed: start_time is required
    assert end_utc is not None  # guaranteed: end_time is required
    services_list = (
        [s.strip() for s in services.split(",") if s.strip()] if services else None
    )
    try:
        prs = pr_dao.find_prs_by_time_range(
            start_utc, end_utc, services_list, time_field
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from None
    if prs is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query GHE prs",
        )

    logger.info(
        "retrieved GHE prs",
        total=len(prs),
        start=str(start_utc),
        end=str(end_utc),
        services=services,
        time_field=time_field,
    )
    return prs


@router.get("/pr/hashes/{org_id}/{repo_id}", status_code=status.HTTP_200_OK)
def get_pr_hashes_by_repo(
    org_id: int,
    repo_id: int,
    pr_dao: GHEPRDAODep,
    metrics: GHEAPIMetricsDep,
) -> list[PRHashInfoResponse]:
    """
    Get description hashes for all PRs in a repository.

    Used by crawler for hash-based change detection to avoid unnecessary LLM calls.
    """
    record = metrics.start_operation("get_hashes", "pull_request") if metrics else None

    try:
        hash_map = pr_dao.get_pr_hashes_by_repo_id(org_id, repo_id)
        if hash_map is None:
            if record:
                try:
                    record(False)
                except Exception:
                    logger.warning("Failed to record metrics")
            logger.error(f"Failed to get PR hashes for org={org_id}, repo={repo_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get PR hashes",
            )

        # Convert dict to list for response
        result = [
            PRHashInfoResponse(
                pull_request_id=info.pull_request_id,
                repository_id=info.repository_id,
                description_hash=info.description_hash,
                pull_request_summary=info.pull_request_summary,
                services=info.services,
            )
            for info in hash_map.values()
        ]

        if record:
            record(True)
        return result
    except HTTPException:
        raise
    except Exception:
        if record:
            try:
                record(False)
            except Exception:
                logger.warning("Failed to record metrics")
        raise
