"""LangGraph state for incident correlation."""

from datetime import datetime
from typing import NotRequired, TypedDict

from pydantic import BaseModel


class ChangeEvent(BaseModel):
    """Lightweight representation of a code change for correlation.

    Holds only the fields needed for LLM-based semantic matching.
    Constructed from GHEPullRequest or JiraIssueRecord API responses.

    start_time: when the event became effective (merged_at for PRs, created_at for TCMRs)
    end_time: optional end boundary (same as start_time for PRs, None for TCMRs)
    """

    id: str
    description: str
    start_time: datetime
    end_time: datetime | None = None
    services: list[str] | None = None


class CorrelationMatch(BaseModel):
    """A single correlation within a group."""

    id: str
    base_score: float | None = None
    final_score: float | None = None
    reasoning: str
    scoring_version: str | None = None


class ServiceCorrelationResult(BaseModel):
    """Service-matched correlation results."""

    biztech_github: list[CorrelationMatch] = []
    jira: list[CorrelationMatch] = []


class LLMCorrelationResult(BaseModel):
    """LLM-scored correlation results."""

    biztech_github: list[CorrelationMatch] = []
    jira: list[CorrelationMatch] = []


class CorrelationGroupResult(BaseModel):
    """Final correlation group with aggregated scoring."""

    biztech_github: list[CorrelationMatch] = []
    jira: list[CorrelationMatch] = []
    base_score: float | None = None
    final_score: float | None = None
    scoring_version: str | None = None


class IncidentCorrelationState(TypedDict):
    """State flowing through the incident correlation graph."""

    incident_id: str
    incident_description: str
    incident_channel_summary: NotRequired[str | None]
    incident_created_at: datetime
    incident_affected_services: list[str]
    jira_time_field: str
    github_time_field: str
    jira_events: list[ChangeEvent]
    github_events: list[ChangeEvent]
    service_correlation_result: ServiceCorrelationResult | None
    llm_correlation_result: LLMCorrelationResult | None
    correlation_group: CorrelationGroupResult | None
