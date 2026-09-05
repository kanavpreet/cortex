"""Unified enrichment hash endpoints.

These endpoints are called by the Enricher service before invoking the LLM.
The Enricher computes a SHA256 hash of the raw source content and compares it
against the hash stored in the database. If the hashes match, the LLM call is
skipped (the existing summary is still valid). If they differ — or no hash
exists yet — enrichment proceeds and the new hash is written alongside the LLM
output by the Scribe service.

Two endpoints are provided:
  POST /v1/enrichment/hashes/batch  — batch fetch for many entities at once
  POST /v1/enrichment/hashes        — single-entity fallback

Both accept a source_type discriminator and return hash column values keyed by
a canonical entity key (json.dumps(entity_id, sort_keys=True)).
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routes.deps import GHEPRDAODep, IncidentIOIncidentDAODep, JiraIssuesDAODep
from common.constants import API_V1_PREFIX
from common.daos import (
    GHEPRDAO,
    IncidentIOHashInfo,
    IncidentIOIncidentDAO,
    JiraIssuesDAO,
)
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix=f"{API_V1_PREFIX}/enrichment", tags=["enrichment"])

# Supported source types — must match keys in enricher config source_mappings
VALID_SOURCE_TYPES = {"incidentio", "jira", "ghe_pr", "incident_channel_summary"}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class BatchHashesRequest(BaseModel):
    """Request body for the batch hash fetch endpoint.

    The Enricher calls this once per source_type per batch of messages,
    passing all entity IDs that had cache misses.
    """

    source_type: str
    # Each dict contains the primary-key fields for one entity.
    # Shape varies by source_type:
    #   incidentio:               {"incident_id": "INC-123"}
    #   jira:                     {"issue_key": "OPS-456"}
    #   ghe_pr:                   {"org_id": 1, "repository_id": 2, "pull_request_id": 3}
    #                             (GitHub API IDs; lookup keys on pull_request_id)
    #   incident_channel_summary: {"reference_id": "INC-1234"}
    entity_ids: list[dict[str, Any]]


class BatchHashesResponse(BaseModel):
    """Response for the batch hash fetch endpoint.

    results maps canonical_entity_key -> {hash_column: hash_value}.

    The canonical key is json.dumps(entity_id, sort_keys=True) so that the
    Enricher can look up results using the same entity_id dict it sent.
    Entities not found in the database are simply absent from results
    (the Enricher treats a missing key as "hash unknown → run enrichment").
    """

    results: dict[str, dict[str, str | None]]


class SingleHashesRequest(BaseModel):
    """Request body for the single-entity hash fetch endpoint.

    Used as a fallback when the batch endpoint is unavailable. Accepts one
    entity_id and returns a flat hash dict (no outer "results" wrapper).
    """

    source_type: str
    entity_id: dict[str, Any]


# ---------------------------------------------------------------------------
# Canonical key helper
# ---------------------------------------------------------------------------


def _canonical_key(entity_id: dict[str, Any]) -> str:
    """Return a stable JSON string for an entity_id dict.

    Sorts keys so that {"a": 1, "b": 2} and {"b": 2, "a": 1} produce the
    same string. This matches the Enricher handler's _canonical_entity_key().
    """
    return json.dumps(entity_id, sort_keys=True)


# ---------------------------------------------------------------------------
# Per-source-type dispatch helpers
# ---------------------------------------------------------------------------


def _fetch_incidentio_hashes(
    dao: IncidentIOIncidentDAO,
    entity_ids: list[dict[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Fetch hash columns for Incident.io incidents.

    Extracts incident_id from each entity_id, calls the dedicated hash-only
    DAO method, and re-keys the results by canonical entity key.

    Only root_cause_summary_hash and description_hash are returned — the two
    columns the Enricher needs to decide whether to re-run each mapping.

    Args:
        dao: IncidentIOIncidentDAO instance.
        entity_ids: List of {"incident_id": str} dicts.

    Returns:
        Dict keyed by canonical entity key -> hash column dict.

    Raises:
        HTTPException 500 if the DAO returns None (database error).
    """
    incident_ids = [eid["incident_id"] for eid in entity_ids]

    # get_incident_hashes_by_ids returns None on database error (consistent with
    # the Jira DAO pattern), so we must check explicitly.
    hash_map = dao.get_incident_hashes_by_ids(incident_ids)
    if hash_map is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Incident.io incident hashes",
        )

    results: dict[str, dict[str, str | None]] = {}
    for eid in entity_ids:
        incident_id = eid["incident_id"]
        if incident_id in hash_map:
            info: IncidentIOHashInfo = hash_map[incident_id]
            results[_canonical_key(eid)] = {
                "root_cause_summary_hash": info.root_cause_summary_hash,
                "description_hash": info.description_hash,
            }
    return results


def _fetch_jira_hashes(
    dao: JiraIssuesDAO,
    entity_ids: list[dict[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Fetch hash columns for JIRA issues.

    Extracts issue_key from each entity_id, calls the DAO, and re-keys
    results by canonical entity key. Only summary_hash and comments_hash are
    included — the Enricher does not need the LLM summary text here.

    Args:
        dao: JiraIssuesDAO instance.
        entity_ids: List of {"issue_key": str} dicts.

    Returns:
        Dict keyed by canonical entity key -> hash column dict.

    Raises:
        HTTPException 500 if the DAO returns None (database error).
    """
    issue_keys = [eid["issue_key"] for eid in entity_ids]

    # get_issue_hashes_by_keys returns None on database error (unlike the
    # IncidentIO DAO which returns {}), so we must check explicitly.
    hash_map = dao.get_issue_hashes_by_keys(issue_keys)
    if hash_map is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch JIRA issue hashes",
        )

    results: dict[str, dict[str, str | None]] = {}
    for eid in entity_ids:
        issue_key = eid["issue_key"]
        if issue_key in hash_map:
            info = hash_map[issue_key]
            results[_canonical_key(eid)] = {
                "summary_hash": info.summary_hash,
                "comments_hash": info.comments_hash,
            }
    return results


def _fetch_ghe_pr_hashes(
    dao: GHEPRDAO,
    entity_ids: list[dict[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Fetch hash columns for GHE pull requests.

    Uses get_pr_hashes_by_ids(), which keys on pull_request_id (the primary key)
    and reads org_id/repo_id inline from each matched row — no join needed.

    The response composite key is "org_id:repo_id:pr_id" (GitHub API IDs),
    matching the key format produced by get_pr_hashes_by_ids().

    Args:
        dao: GHEPRDAO instance.
        entity_ids: List of {"org_id": int, "repository_id": int,
                             "pull_request_id": int} dicts using GitHub API IDs.

    Returns:
        Dict keyed by canonical entity key -> {"description_hash": value}.

    Raises:
        HTTPException 500 if the DAO returns None (database error).
    """
    # The DAO expects list[dict[str, int]] and handles its own batching
    hash_map = dao.get_pr_hashes_by_ids(entity_ids)
    if hash_map is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch GHE PR hashes",
        )

    results: dict[str, dict[str, str | None]] = {}
    for eid in entity_ids:
        # Build the composite key in the same format as the DAO response:
        # "org_id:repo_id:pull_request_id" (all GitHub API IDs)
        composite = f"{eid['org_id']}:{eid['repository_id']}:{eid['pull_request_id']}"
        if composite in hash_map:
            info = hash_map[composite]
            results[_canonical_key(eid)] = {
                "description_hash": info.description_hash,
            }
    return results


def _fetch_incident_channel_summary_hashes(
    dao: IncidentIOIncidentDAO,
    entity_ids: list[dict[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Fetch hash columns for the incident channel summary source.

    Extracts reference_id from each entity_id — this source's enrichment_key
    (see common/datasources/incident_channel_summary.py), unlike incidentio
    which keys on incident_id — and re-keys results by canonical entity key.

    Args:
        dao: IncidentIOIncidentDAO instance (shares the incidentio_incidents
            table; the read query does not depend on which spec it was
            constructed with).
        entity_ids: List of {"reference_id": str} dicts.

    Returns:
        Dict keyed by canonical entity key -> {"incident_channel_summary_hash": value}.

    Raises:
        HTTPException 500 if the DAO returns None (database error).
    """
    reference_ids = [eid["reference_id"] for eid in entity_ids]

    hash_map = dao.get_incident_channel_summary_hashes_by_reference_ids(reference_ids)
    if hash_map is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch incident channel summary hashes",
        )

    results: dict[str, dict[str, str | None]] = {}
    for eid in entity_ids:
        reference_id = eid["reference_id"]
        if reference_id in hash_map:
            results[_canonical_key(eid)] = {
                "incident_channel_summary_hash": hash_map[reference_id],
            }
    return results


def _dispatch(
    source_type: str,
    entity_ids: list[dict[str, Any]],
    incidentio_dao: IncidentIOIncidentDAO,
    jira_dao: JiraIssuesDAO,
    ghe_pr_dao: GHEPRDAO,
) -> dict[str, dict[str, str | None]]:
    """Route a hash fetch request to the appropriate DAO based on source_type.

    Args:
        source_type: One of "incidentio", "jira", "ghe_pr", "incident_channel_summary".
        entity_ids: List of entity primary-key dicts for the given source_type.
        incidentio_dao: DAO for Incident.io incidents (also serves
            incident_channel_summary, which shares the same table).
        jira_dao: DAO for JIRA issues.
        ghe_pr_dao: DAO for GHE pull requests.

    Returns:
        Dict of {canonical_entity_key: {hash_col: hash_value}}.
    """
    if source_type == "incidentio":
        return _fetch_incidentio_hashes(incidentio_dao, entity_ids)
    elif source_type == "jira":
        return _fetch_jira_hashes(jira_dao, entity_ids)
    elif source_type == "incident_channel_summary":
        return _fetch_incident_channel_summary_hashes(incidentio_dao, entity_ids)
    else:
        # source_type == "ghe_pr" (already validated before dispatch)
        return _fetch_ghe_pr_hashes(ghe_pr_dao, entity_ids)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/hashes/batch", status_code=status.HTTP_200_OK)
def batch_fetch_hashes(
    request: BatchHashesRequest,
    incidentio_dao: IncidentIOIncidentDAODep,
    jira_dao: JiraIssuesDAODep,
    ghe_pr_dao: GHEPRDAODep,
) -> BatchHashesResponse:
    """Batch-fetch existing entity hashes for the Enricher service.

    Called by the Enricher before invoking the LLM. Returns a map of
    canonical entity key -> hash columns for all entities found in the DB.
    Entities not present in the DB are simply absent from the response —
    the Enricher treats a missing key as "no existing hash → run enrichment".

    Args:
        request: source_type + list of entity_id dicts.

    Returns:
        BatchHashesResponse with results keyed by canonical entity key.

    Raises:
        400 if source_type is not one of: incidentio, jira, ghe_pr, incident_channel_summary.
        500 if the database query fails.
    """
    if request.source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid source_type '{request.source_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"
            ),
        )

    if not request.entity_ids:
        return BatchHashesResponse(results={})

    results = _dispatch(
        request.source_type,
        request.entity_ids,
        incidentio_dao,
        jira_dao,
        ghe_pr_dao,
    )
    logger.info(
        "batch hash fetch completed",
        source_type=request.source_type,
        requested=len(request.entity_ids),
        found=len(results),
    )
    return BatchHashesResponse(results=results)


@router.post("/hashes", status_code=status.HTTP_200_OK)
def fetch_single_hash(
    request: SingleHashesRequest,
    incidentio_dao: IncidentIOIncidentDAODep,
    jira_dao: JiraIssuesDAODep,
    ghe_pr_dao: GHEPRDAODep,
) -> dict[str, str | None]:
    """Fetch existing hash columns for a single entity.

    Fallback endpoint used by the Enricher when the batch endpoint is
    unavailable. Returns a flat dict of {hash_col: hash_value} for the
    given entity, or {} if the entity is not found in the database.

    Args:
        request: source_type + a single entity_id dict.

    Returns:
        Flat hash column dict, e.g. {"root_cause_summary_hash": "abc123"}.
        Returns {} when the entity does not exist in the DB.

    Raises:
        400 if source_type is not one of: incidentio, jira, ghe_pr, incident_channel_summary.
        500 if the database query fails.
    """
    if request.source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid source_type '{request.source_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}"
            ),
        )

    # Reuse the batch dispatch logic with a single-element list
    results = _dispatch(
        request.source_type,
        [request.entity_id],
        incidentio_dao,
        jira_dao,
        ghe_pr_dao,
    )

    # Extract and return the single entity's hash dict (or {} if not found)
    canon = _canonical_key(request.entity_id)
    return results.get(canon, {})
