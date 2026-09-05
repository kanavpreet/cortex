"""Correlation read endpoints."""

from fastapi import APIRouter, status

from api.routes.deps import ReliabilityCorrelationDAODep
from common.constants import API_V1_PREFIX
from common.models.reliability_correlation import ReliabilityCorrelation
from common.utils.list_utils import deduplicate_by_priority

router = APIRouter(
    prefix=f"{API_V1_PREFIX}/correlation/reliability",
    tags=["correlation"],
)


@router.get("/events/{anchor_entity_id}", status_code=status.HTTP_200_OK)
def get_correlation_events(
    anchor_entity_id: str,
    correlation_dao: ReliabilityCorrelationDAODep,
) -> list[ReliabilityCorrelation]:
    """Get all correlation events for an anchor entity."""
    correlations = correlation_dao.find_by_anchor(anchor_entity_id)
    return deduplicate_by_priority(
        correlations,
        key_field="entity_id",
        priority_field="correlation_type",
        priority=["SERVICE_MATCH", "LLM"],
    )
