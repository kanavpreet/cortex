"""Unit tests for incident correlation scoring functions."""

from datetime import datetime, timedelta

from enigmatologist.correlations.reliability.incident.scoring import (
    _SCORE_TIERS,
    GROUP_SCORE_CAP,
    LLM_SCORE_CAP,
    SCORING_VERSION,
    SERVICE_SCORE_CAP,
    _time_based_score,
    compute_correlation_group,
    score_llm_correlations,
    score_service_correlations,
)
from enigmatologist.correlations.reliability.incident.state import (
    ChangeEvent,
    CorrelationMatch,
    IncidentCorrelationState,
    LLMCorrelationResult,
    ServiceCorrelationResult,
)


class TestTimeBasedScore:
    """Tests for _time_based_score function."""

    def _make_times(self, minutes: float) -> tuple[datetime, datetime]:
        """Create incident and event times separated by given minutes."""
        incident = datetime(2025, 1, 15, 12, 0, 0)
        event = incident - timedelta(minutes=minutes)
        return incident, event

    def test_within_30_minutes(self) -> None:
        """Events within 30 minutes get score 1.00."""
        incident, event = self._make_times(15)
        assert _time_based_score(incident, event) == 1.00

    def test_at_30_minutes(self) -> None:
        """Events exactly at 30 minutes get score 1.00."""
        incident, event = self._make_times(30)
        assert _time_based_score(incident, event) == 1.00

    def test_between_30_and_60_minutes(self) -> None:
        """Events between 30 and 60 minutes get score 0.80."""
        incident, event = self._make_times(45)
        assert _time_based_score(incident, event) == 0.80

    def test_between_1_and_2_hours(self) -> None:
        """Events between 60 and 120 minutes get score 0.60."""
        incident, event = self._make_times(90)
        assert _time_based_score(incident, event) == 0.60

    def test_between_2_and_4_hours(self) -> None:
        """Events between 120 and 240 minutes get score 0.40."""
        incident, event = self._make_times(180)
        assert _time_based_score(incident, event) == 0.40

    def test_between_4_and_8_hours(self) -> None:
        """Events between 240 and 480 minutes get score 0.20."""
        incident, event = self._make_times(360)
        assert _time_based_score(incident, event) == 0.20

    def test_between_8_and_16_hours(self) -> None:
        """Events between 480 and 960 minutes get score 0.15."""
        incident, event = self._make_times(720)
        assert _time_based_score(incident, event) == 0.15

    def test_between_16_and_24_hours(self) -> None:
        """Events between 960 and 1440 minutes get score 0.10."""
        incident, event = self._make_times(1200)
        assert _time_based_score(incident, event) == 0.10

    def test_beyond_24_hours_uses_lowest_tier(self) -> None:
        """Events beyond all tiers get the lowest tier score."""
        incident, event = self._make_times(2880)  # 48 hours
        assert _time_based_score(incident, event) == _SCORE_TIERS[-1][1]

    def test_event_after_incident_scores_zero(self) -> None:
        """Events after the incident score 0 — they cannot be its cause."""
        incident = datetime(2025, 1, 15, 12, 0, 0)
        event = incident + timedelta(minutes=10)  # 10 min AFTER incident
        assert _time_based_score(incident, event) == 0.0

    def test_event_at_exact_incident_time_scores_highest(self) -> None:
        """An event at exactly the incident time (diff=0) scores the highest tier."""
        incident = datetime(2025, 1, 15, 12, 0, 0)
        assert _time_based_score(incident, incident) == 1.00


def _make_state_with_service_result(
    minutes_ago: float,
) -> IncidentCorrelationState:
    """Build a minimal state with a service correlation result."""
    incident_time = datetime(2025, 1, 15, 12, 0, 0)
    event_time = incident_time - timedelta(minutes=minutes_ago)
    return IncidentCorrelationState(
        incident_id="INC-99",
        incident_description="test incident",
        incident_created_at=incident_time,
        incident_affected_services=["svc-a"],
        jira_time_field="created_at",
        github_time_field="created_at",
        jira_events=[],
        github_events=[
            ChangeEvent(id="PR-1", description="fix", start_time=event_time),
        ],
        service_correlation_result=ServiceCorrelationResult(
            biztech_github=[
                CorrelationMatch(id="PR-1", reasoning="Matched by affected service."),
            ],
            jira=[],
        ),
        llm_correlation_result=None,
        correlation_group=None,
    )


class TestScoreServiceCorrelations:
    """Tests for score_service_correlations."""

    def test_scores_github_matches(self) -> None:
        """Service-matched GitHub events are scored by time proximity."""
        state = _make_state_with_service_result(minutes_ago=20)
        result = score_service_correlations(state)
        svc_result = result["service_correlation_result"]
        assert svc_result is not None
        match = svc_result.biztech_github[0]
        assert match.base_score == 1.00
        assert match.final_score == 1.00 * SERVICE_SCORE_CAP
        assert match.scoring_version == SCORING_VERSION

    def test_no_result_returns_state_unchanged(self) -> None:
        """When there's no service_correlation_result, state is returned as-is."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=None,
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = score_service_correlations(state)
        assert result["service_correlation_result"] is None

    def test_no_incident_time_returns_state_unchanged(self) -> None:
        """When incident_created_at is missing, state is returned as-is."""
        state = _make_state_with_service_result(minutes_ago=20)
        state["incident_created_at"] = None  # type: ignore[typeddict-item]
        result = score_service_correlations(state)
        # Match should not have been scored
        svc_result = result["service_correlation_result"]
        assert svc_result is not None
        match = svc_result.biztech_github[0]
        assert match.base_score is None

    def test_incident_time_as_string(self) -> None:
        """incident_created_at as ISO string is parsed correctly."""
        state = _make_state_with_service_result(minutes_ago=20)
        state["incident_created_at"] = "2025-01-15T12:00:00"  # type: ignore[typeddict-item]
        result = score_service_correlations(state)
        svc_result = result["service_correlation_result"]
        assert svc_result is not None
        match = svc_result.biztech_github[0]
        assert match.base_score == 1.00

    def test_jira_matches_scored(self) -> None:
        """Service-matched Jira events are scored by time proximity."""
        incident_time = datetime(2025, 1, 15, 12, 0, 0)
        event_time = incident_time - timedelta(hours=3)
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=incident_time,
            incident_affected_services=["svc-a"],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[
                ChangeEvent(id="TCMR-1", description="change", start_time=event_time),
            ],
            github_events=[],
            service_correlation_result=ServiceCorrelationResult(
                biztech_github=[],
                jira=[
                    CorrelationMatch(
                        id="TCMR-1", reasoning="Matched by affected service."
                    ),
                ],
            ),
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = score_service_correlations(state)
        svc_result = result["service_correlation_result"]
        assert svc_result is not None
        match = svc_result.jira[0]
        assert match.base_score == 0.40  # 3 hours = 180 min, falls in 120-240 tier
        assert match.final_score == 0.40 * SERVICE_SCORE_CAP


class TestScoreLLMCorrelations:
    """Tests for score_llm_correlations."""

    def test_scores_llm_matches(self) -> None:
        """LLM matches get final_score = base_score * LLM_SCORE_CAP."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=None,
            llm_correlation_result=LLMCorrelationResult(
                biztech_github=[
                    CorrelationMatch(
                        id="PR-1", base_score=0.8, reasoning="same component"
                    ),
                ],
                jira=[],
            ),
            correlation_group=None,
        )
        result = score_llm_correlations(state)
        llm_result = result["llm_correlation_result"]
        assert llm_result is not None
        match = llm_result.biztech_github[0]
        assert match.final_score == 0.8 * LLM_SCORE_CAP
        assert match.scoring_version == SCORING_VERSION

    def test_no_result_returns_state_unchanged(self) -> None:
        """When there's no llm_correlation_result, state is returned as-is."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=None,
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = score_llm_correlations(state)
        assert result["llm_correlation_result"] is None

    def test_none_base_score_not_scored(self) -> None:
        """Matches with None base_score are not scored."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=None,
            llm_correlation_result=LLMCorrelationResult(
                biztech_github=[
                    CorrelationMatch(
                        id="PR-1", base_score=None, reasoning="weak match"
                    ),
                ],
                jira=[],
            ),
            correlation_group=None,
        )
        result = score_llm_correlations(state)
        llm_result = result["llm_correlation_result"]
        assert llm_result is not None
        match = llm_result.biztech_github[0]
        assert match.final_score is None


class TestComputeCorrelationGroup:
    """Tests for compute_correlation_group."""

    def test_merges_service_and_llm(self) -> None:
        """Group merges matches from both service and LLM paths."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=["svc-a"],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=ServiceCorrelationResult(
                biztech_github=[
                    CorrelationMatch(
                        id="PR-1",
                        reasoning="service match",
                        final_score=0.9,
                    ),
                ],
                jira=[],
            ),
            llm_correlation_result=LLMCorrelationResult(
                biztech_github=[],
                jira=[
                    CorrelationMatch(
                        id="TCMR-1",
                        reasoning="llm match",
                        final_score=0.48,
                    ),
                ],
            ),
            correlation_group=None,
        )
        result = compute_correlation_group(state)
        group = result["correlation_group"]
        assert group is not None
        assert len(group.biztech_github) == 1
        assert len(group.jira) == 1
        avg = (0.9 + 0.48) / 2
        assert group.base_score == round(min(avg, GROUP_SCORE_CAP), 4)
        assert group.final_score == group.base_score
        assert group.scoring_version == SCORING_VERSION

    def test_no_matches_returns_state_without_group(self) -> None:
        """When no matches exist, correlation_group is not set."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=None,
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = compute_correlation_group(state)
        assert result["correlation_group"] is None

    def test_group_score_capped(self) -> None:
        """Group score is capped at GROUP_SCORE_CAP."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=ServiceCorrelationResult(
                biztech_github=[
                    CorrelationMatch(id="PR-1", reasoning="match", final_score=0.95),
                    CorrelationMatch(id="PR-2", reasoning="match", final_score=0.95),
                ],
                jira=[],
            ),
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = compute_correlation_group(state)
        group = result["correlation_group"]
        assert group is not None
        assert group.base_score == GROUP_SCORE_CAP

    def test_no_scored_matches_gives_zero_score(self) -> None:
        """Matches with no final_score produce group score of 0.0."""
        state = IncidentCorrelationState(
            incident_id="INC-99",
            incident_description="test",
            incident_created_at=datetime(2025, 1, 15, 12, 0, 0),
            incident_affected_services=[],
            jira_time_field="created_at",
            github_time_field="created_at",
            jira_events=[],
            github_events=[],
            service_correlation_result=ServiceCorrelationResult(
                biztech_github=[
                    CorrelationMatch(id="PR-1", reasoning="match", final_score=None),
                ],
                jira=[],
            ),
            llm_correlation_result=None,
            correlation_group=None,
        )
        result = compute_correlation_group(state)
        group = result["correlation_group"]
        assert group is not None
        assert group.base_score == 0.0
