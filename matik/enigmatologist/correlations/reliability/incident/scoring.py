"""Scoring functions for incident correlation.

Bump SCORING_VERSION when scoring logic changes.
"""

from datetime import datetime

from .state import (
    CorrelationGroupResult,
    CorrelationMatch,
    IncidentCorrelationState,
)

# Bump this when scoring logic changes
SCORING_VERSION = "incident_v1"

# Caps applied to individual final_score by path
SERVICE_SCORE_CAP = 0.90
LLM_SCORE_CAP = 0.60

# Group score cap
GROUP_SCORE_CAP = 0.90

# Time-based scoring tiers for service-matched correlations
# | Time Difference | base_score | Description        |
# |-----------------|------------|--------------------|
# | <= 30 minutes   | 1.00       | Very strong signal |
# | 30-60 minutes   | 0.80       | Strong signal      |
# | 1-2 hours       | 0.60       | Moderate signal    |
# | 2-4 hours       | 0.40       | Weak signal        |
# | 4-8 hours       | 0.20       | Very weak signal   |
# | 8-16 hours      | 0.15       | Minimal signal     |
# | 16-24 hours     | 0.10       | Lowest signal      |
_SCORE_TIERS: list[tuple[float, float]] = [
    (30, 1.00),
    (60, 0.80),
    (120, 0.60),
    (240, 0.40),
    (480, 0.20),
    (960, 0.15),
    (1440, 0.10),
]


def _time_based_score(incident_time: datetime, event_time: datetime) -> float:
    """Compute a score based on temporal proximity to the incident.

    Events after the incident creation time score 0 — a change that happened
    after the incident cannot be its cause.
    """
    diff_minutes = (incident_time - event_time).total_seconds() / 60
    if diff_minutes < 0:
        return 0.0
    for threshold, score in _SCORE_TIERS:
        if diff_minutes <= threshold:
            return score
    return _SCORE_TIERS[-1][1]


def score_service_correlations(
    state: IncidentCorrelationState,
) -> IncidentCorrelationState:
    """Score service-matched correlations based on temporal proximity.

    Sets base_score from time tier, final_score = base_score * SERVICE_SCORE_CAP.
    """
    result = state.get("service_correlation_result")
    if not result:
        return state

    incident_time = state.get("incident_created_at")
    if not incident_time:
        return state

    if isinstance(incident_time, str):  # type: ignore[unreachable]
        incident_time = datetime.fromisoformat(incident_time)  # type: ignore[unreachable]

    # Build lookup from event ID to start_time
    event_map: dict[str, datetime] = {}
    for e in state.get("github_events", []):
        event_map[e.id] = e.start_time
    for e in state.get("jira_events", []):
        event_map[e.id] = e.start_time

    for match in result.biztech_github + result.jira:
        ts = event_map.get(match.id)
        if ts:
            match.base_score = _time_based_score(incident_time, ts)
            match.final_score = match.base_score * SERVICE_SCORE_CAP
            match.scoring_version = SCORING_VERSION

    return state


def score_llm_correlations(
    state: IncidentCorrelationState,
) -> IncidentCorrelationState:
    """Score LLM correlations by applying the LLM cap.

    The LLM already provides a score — that becomes base_score.
    final_score = base_score * LLM_SCORE_CAP.
    """
    result = state.get("llm_correlation_result")
    if not result:
        return state

    for match in result.biztech_github + result.jira:
        if match.base_score is not None:
            match.final_score = match.base_score * LLM_SCORE_CAP
            match.scoring_version = SCORING_VERSION

    return state


def compute_correlation_group(
    state: IncidentCorrelationState,
) -> IncidentCorrelationState:
    """Merge service and LLM correlations into a single group with an overall score.

    Group base_score = min(avg(individual final_scores), GROUP_SCORE_CAP).
    Group final_score = base_score (default NEUTRAL feedback).
    """
    all_matches: list[CorrelationMatch] = []
    github_matches: list[CorrelationMatch] = []
    jira_matches: list[CorrelationMatch] = []

    service = state.get("service_correlation_result")
    if service:
        github_matches.extend(service.biztech_github)
        jira_matches.extend(service.jira)

    llm = state.get("llm_correlation_result")
    if llm:
        github_matches.extend(llm.biztech_github)
        jira_matches.extend(llm.jira)

    all_matches = github_matches + jira_matches

    if not all_matches:
        return state

    # Compute group base_score from individual final_scores
    scored = [m.final_score for m in all_matches if m.final_score is not None]
    if scored:
        avg_score = sum(scored) / len(scored)
        group_base = min(avg_score, GROUP_SCORE_CAP)
    else:
        group_base = 0.0

    state["correlation_group"] = CorrelationGroupResult(
        biztech_github=github_matches,
        jira=jira_matches,
        base_score=round(group_base, 4),
        final_score=round(group_base, 4),  # NEUTRAL feedback by default
        scoring_version=SCORING_VERSION,
    )

    return state
