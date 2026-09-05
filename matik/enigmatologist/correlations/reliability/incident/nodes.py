"""Graph node implementations for incident correlation."""

import json
import re
from datetime import datetime, timedelta

import httpx
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace

from common.clients import (
    BedrockClient,
    FacadeClient,
    facade_system_message,
    facade_user_message,
)
from common.llm_tracing import traced_llm_operation
from common.metrics import CorrelationMetrics
from common.models.enigmatologist_config import EnigmatologistConfig
from common.utils import log_utils
from common.utils.service_auth import (
    build_signature_headers,
    log_signature_rejection_401,
)

from .state import (
    ChangeEvent,
    CorrelationMatch,
    IncidentCorrelationState,
    LLMCorrelationResult,
    ServiceCorrelationResult,
)

logger = log_utils.get_logger(__name__)

_tracer = trace.get_tracer(__name__)

# Fallback API base URL when config is not available
_DEFAULT_API_BASE_URL = "http://api:8080"


def _signature_headers(config: RunnableConfig, target: str) -> dict[str, str] | None:
    """Phase 1c signature headers for a GET to `target`, or None if unconfigured.

    `target` must be the exact path+query that will be sent (e.g.
    `httpx.URL(...).raw_path.decode()`), not just the bare path — the
    signature covers the query string too, so it must match byte-for-byte.

    api_service_secret is threaded through configurable by run_correlation()
    (see correlations/reliability/incident/main.py) alongside api_base_url.

    Any new fetch node added to this module must call this helper itself —
    signing isn't automatic the way it is for MatikApiClient callers, since
    these fetch nodes call the API via raw httpx instead. Tracked for
    consolidation onto MatikApiClient in a follow-up (access-posture doc,
    Decisions still open), pending a configurable timeout on
    MatikApiClient.get_request().
    """
    secret = config["configurable"].get("api_service_secret")
    return build_signature_headers(secret, "GET", target, b"") if secret else None


# =============================================================================
# Fetch nodes — pull data from the Matik API
# =============================================================================


def fetch_jira_issues(
    state: IncidentCorrelationState,
    config: RunnableConfig,
) -> IncidentCorrelationState:
    """Pull issues from jira table and convert to lightweight ChangeEvents.

    Computes a lookback window from the incident timestamp.
    TCMRs use a longer window because changes can take time to surface.
    """
    cfg: EnigmatologistConfig | None = config["configurable"].get(
        "enigmatologist_config"
    )
    api_base_url = config["configurable"].get("api_base_url") or _DEFAULT_API_BASE_URL
    metrics: CorrelationMetrics | None = config["configurable"].get(
        "correlation_metrics"
    )

    incident_time = state.get("incident_created_at")
    if not incident_time:
        logger.warning("no incident_created_at in state, skipping Jira fetch")
        state["jira_events"] = []
        return state

    if isinstance(incident_time, str):  # type: ignore[unreachable]
        incident_time = datetime.fromisoformat(incident_time)  # type: ignore[unreachable]

    lookback = cfg.lookback_hours_jira if cfg else 24
    start_time = incident_time - timedelta(hours=lookback)
    end_time = incident_time

    params: dict[str, str] = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }

    # Don't filter by services here — fetch all events in the window.
    # Service matching and LLM will handle filtering downstream.

    time_field = state.get("jira_time_field")
    if time_field:
        params["time_field"] = time_field

    timeout = cfg.matik_api_read_timeout if cfg else 10.0
    record_node = metrics.start_node("fetch_jira_issues") if metrics else None
    # Query params are baked into the URL up front so the signature covers
    # the exact path+query bytes that will be sent (see _signature_headers).
    url = httpx.URL(f"{api_base_url}/v1/jira/issues", params=params)
    with _tracer.start_as_current_span("fetch_jira_issues") as span:
        try:
            resp = httpx.get(
                url,
                timeout=timeout,
                headers=_signature_headers(config, url.raw_path.decode()),
            )
            resp.raise_for_status()
            state["jira_events"] = [
                ChangeEvent(
                    id=issue["issue_key"],
                    description=issue.get("issue_summary") or issue.get("summary", ""),
                    start_time=issue.get("tcmr_planned_start_date")
                    or issue["created_at"],
                    end_time=issue.get("tcmr_planned_end_date"),
                    services=issue.get("services"),
                )
                for issue in resp.json()
            ]
            span.set_attribute("event_count", len(state["jira_events"]))
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                log_signature_rejection_401(logger, "fetching JIRA issues")
            else:
                logger.warning(
                    "failed to fetch JIRA issues",
                    status_code=err.response.status_code,
                    exc_info=True,
                )
            state["jira_events"] = []
        except Exception:
            logger.warning("failed to fetch JIRA issues", exc_info=True)
            state["jira_events"] = []
        finally:
            if record_node:
                record_node()

    return state


def fetch_biztech_github_prs(
    state: IncidentCorrelationState,
    config: RunnableConfig,
) -> IncidentCorrelationState:
    """Pull PRs from biztech github table and convert to lightweight ChangeEvents.

    Computes a lookback window from the incident timestamp.
    PRs use a shorter window because code deploys have immediate effect.
    """
    cfg: EnigmatologistConfig | None = config["configurable"].get(
        "enigmatologist_config"
    )
    api_base_url = config["configurable"].get("api_base_url") or _DEFAULT_API_BASE_URL
    metrics: CorrelationMetrics | None = config["configurable"].get(
        "correlation_metrics"
    )

    incident_time = state.get("incident_created_at")
    if not incident_time:
        logger.warning("no incident_created_at in state, skipping GHE PR fetch")
        state["github_events"] = []
        return state

    if isinstance(incident_time, str):  # type: ignore[unreachable]
        incident_time = datetime.fromisoformat(incident_time)  # type: ignore[unreachable]

    lookback = cfg.lookback_hours_github if cfg else 6
    start_time = incident_time - timedelta(hours=lookback)
    end_time = incident_time

    params: dict[str, str] = {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }

    # Don't filter by services here — fetch all events in the window.
    # Service matching and LLM will handle filtering downstream.

    time_field = state.get("github_time_field")
    if time_field:
        params["time_field"] = time_field

    timeout = cfg.matik_api_read_timeout if cfg else 10.0
    record_node = metrics.start_node("fetch_biztech_github_prs") if metrics else None
    # Query params are baked into the URL up front so the signature covers
    # the exact path+query bytes that will be sent (see _signature_headers).
    url = httpx.URL(f"{api_base_url}/v1/ghe/pr", params=params)
    with _tracer.start_as_current_span("fetch_biztech_github_prs") as span:
        try:
            resp = httpx.get(
                url,
                timeout=timeout,
                headers=_signature_headers(config, url.raw_path.decode()),
            )
            resp.raise_for_status()
            state["github_events"] = [
                ChangeEvent(
                    id=str(pr["pull_request_id"]),
                    description=pr.get("pull_request_summary") or pr.get("title", ""),
                    start_time=pr.get("merged_at") or pr["created_at"],
                    end_time=pr.get("merged_at") or pr["created_at"],
                    services=pr.get("services"),
                )
                for pr in resp.json()
            ]
            span.set_attribute("event_count", len(state["github_events"]))
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                log_signature_rejection_401(logger, "fetching GHE PRs")
            else:
                logger.warning(
                    "failed to fetch GHE prs",
                    status_code=err.response.status_code,
                    exc_info=True,
                )
            state["github_events"] = []
        except Exception:
            logger.warning("failed to fetch GHE prs", exc_info=True)
            state["github_events"] = []
        finally:
            if record_node:
                record_node()

    return state


# =============================================================================
# Routing — conditional edge functions
# =============================================================================


def is_service_exists(state: IncidentCorrelationState) -> bool:
    """Check if affected services is defined in the incident."""
    return bool(state.get("incident_affected_services"))


def has_change_events(state: IncidentCorrelationState) -> bool:
    """Whether any source fetched change events to correlate against."""
    return any(
        isinstance(v, list) and v and isinstance(v[0], ChangeEvent)
        for v in state.values()
    )


def route_after_fetch(state: IncidentCorrelationState) -> str:
    """Route after fetching change events.

    Skips all correlation work when nothing was fetched — correlating an empty
    change list only ever yields empty results, so there is nothing worth an LLM
    call. Otherwise takes the service-matching branch when the incident names
    affected services, else goes straight to LLM correlation.
    """
    if not has_change_events(state):
        return "skip"
    return "service" if is_service_exists(state) else "llm"


# =============================================================================
# Correlation assignment — populate correlation results on state
# =============================================================================


def _has_service_overlap(
    event_services: list[str] | None, incident_services: list[str]
) -> bool:
    """Check if an event shares at least one service with the incident."""
    if not event_services or not incident_services:
        return False
    return bool(
        {s.lower() for s in event_services} & {s.lower() for s in incident_services}
    )


def assign_correlations_by_service(
    state: IncidentCorrelationState,
) -> IncidentCorrelationState:
    """Assign service-matched events as correlation results.

    Filters events in-memory by exact service overlap with the incident's
    affected services.
    """
    incident_services = state.get("incident_affected_services", [])
    github = [
        e
        for e in state.get("github_events", [])
        if _has_service_overlap(e.services, incident_services)
    ]
    jira = [
        e
        for e in state.get("jira_events", [])
        if _has_service_overlap(e.services, incident_services)
    ]

    if not github and not jira:
        return state

    state["service_correlation_result"] = ServiceCorrelationResult(
        biztech_github=[
            CorrelationMatch(id=e.id, reasoning="Matched by affected service.")
            for e in github
        ],
        jira=[
            CorrelationMatch(id=e.id, reasoning="Matched by affected service.")
            for e in jira
        ],
    )
    return state


def _build_user_prompt(state: IncidentCorrelationState) -> str:
    """Build the user prompt from graph state."""
    github_changes = [
        {
            "id": e.id,
            "description": e.description,
            "timestamp": e.start_time.isoformat(),
            "services": e.services or [],
        }
        for e in state.get("github_events", [])
    ]
    jira_changes = [
        {
            "id": e.id,
            "description": e.description,
            "timestamp": e.start_time.isoformat(),
            "services": e.services or [],
        }
        for e in state.get("jira_events", [])
    ]

    incident_services = state.get("incident_affected_services", [])

    return (
        f"Incident description: {state.get('incident_description')}\n"
        f"Incident channel summary: {state.get('incident_channel_summary')}\n"
        f"Incident created_at: {state.get('incident_created_at')}\n"
        f"Incident affected services: {json.dumps(incident_services)}\n\n"
        f"Github PRs: {json.dumps(github_changes)}\n"
        f"Jira issues: {json.dumps(jira_changes)}"
    )


def parse_llm_correlations(
    response: str,
    min_score: float,
    speculative_patterns: list[str],
    speculative_max_score: float = 0.7,
) -> LLMCorrelationResult:
    """Parse and filter the raw LLM correlation response into an LLMCorrelationResult.

    Strips markdown code fences, maps the LLM's ``score`` to ``base_score``, and
    drops items below ``min_score``. Speculative reasoning (matching
    ``speculative_patterns``) only drops a match scored below
    ``speculative_max_score`` — at or above it the LLM's score is trusted over
    hedging words, so a confident match whose reasoning says "could cause" is
    kept rather than discarded. Raises (JSON/validation errors) when the response
    is not the required structure. Shared by the production node and the
    correlation eval so both apply identical parsing.
    """
    # Strip markdown code fences the LLM sometimes wraps around JSON
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]  # drop ```json line
        cleaned = cleaned.rsplit("```", 1)[0]  # drop trailing ```
    raw = json.loads(cleaned)
    # LLM returns "score" — map to "base_score" for our model.
    # Drop items below threshold. Speculative reasoning only drops LOW-confidence
    # matches: a match scored >= speculative_max_score is kept even if it hedges,
    # since the LLM doesn't always follow exclusion rules and the score is the
    # stronger signal for confident matches.
    for category in ("biztech_github", "jira"):
        for item in raw.get(category, []):
            if "score" in item:
                item["base_score"] = item.pop("score")
        raw[category] = [
            item
            for item in raw.get(category, [])
            if (item.get("base_score") or 0) >= min_score
            and not (
                (item.get("base_score") or 0) < speculative_max_score
                and any(
                    re.search(p, item.get("reasoning") or "", re.IGNORECASE)
                    for p in speculative_patterns
                )
            )
        ]
    return LLMCorrelationResult.model_validate(raw)


async def assign_correlations_by_llm(
    state: IncidentCorrelationState,
    config: RunnableConfig,
) -> IncidentCorrelationState:
    """Call LLM to correlate incident with recent changes.

    Args:
        state: Graph state containing incident info and fetched events
        config: LangGraph RunnableConfig with llm_client in configurable
    """
    llm_client: FacadeClient | BedrockClient = config["configurable"]["llm_client"]
    cfg: EnigmatologistConfig | None = config["configurable"].get(
        "enigmatologist_config"
    )
    metrics: CorrelationMetrics | None = config["configurable"].get(
        "correlation_metrics"
    )
    min_score = cfg.min_llm_score if cfg else 0.3
    speculative = cfg.speculative_patterns if cfg else []
    speculative_max = cfg.speculative_max_score if cfg else 0.7
    system_prompt = cfg.correlation_system_prompt if cfg else ""
    if not system_prompt:
        logger.warning(
            "no correlation_system_prompt configured, skipping LLM correlation"
        )
        return state

    messages = [
        facade_system_message(system_prompt),
        facade_user_message(_build_user_prompt(state)),
    ]

    incident_created_at = state.get("incident_created_at")
    if isinstance(incident_created_at, str):  # type: ignore[unreachable]
        incident_created_at = datetime.fromisoformat(incident_created_at)  # type: ignore[unreachable]

    record_node = metrics.start_node("assign_correlations_by_llm") if metrics else None
    try:
        with traced_llm_operation(
            "incident_correlation",
            source="correlation",
            entity_id={"incident_id": state["incident_id"]},
            entity_created_at=incident_created_at,
        ):
            response = await llm_client.send_message_with_retry(
                model=None,
                messages=messages,
                operation="incident_correlation",
            )
        result = parse_llm_correlations(
            response, min_score, speculative, speculative_max
        )
        if result.biztech_github or result.jira:
            state["llm_correlation_result"] = result
    except Exception:
        logger.warning(
            "facade correlation failed",
            raw_response=response if "response" in dir() else None,
            exc_info=True,
        )
        state["llm_correlation_result"] = None
    finally:
        if record_node:
            record_node()

    return state
