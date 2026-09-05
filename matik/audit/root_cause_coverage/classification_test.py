"""Tests for correlation classification (all deterministic, no LLM)."""

from audit.root_cause_coverage.classification import (
    build_correlation_rows,
    classify_by_service_match,
    classify_root_cause_matches,
    is_root_cause_match,
)
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.root_cause_audit import VerifiedEntity


def _correlation(
    entity_type: str,
    entity_id: str,
    services: list[str] | None = None,
    base_score: float | None = None,
) -> ReliabilityCorrelation:
    return ReliabilityCorrelation(
        anchor_entity_id="INC-1234",
        correlation_type="LLM",
        entity_type=entity_type,
        entity_id=entity_id,
        services=services,
        base_score=base_score,
    )


def _gold(
    entity_type: str = "github_pr", entity_id: str = "987654321"
) -> VerifiedEntity:
    return VerifiedEntity(entity_type=entity_type, entity_id=entity_id)


# ---------------------------------------------------------------------------
# is_root_cause_match
# ---------------------------------------------------------------------------


def test_matches_on_exact_entity_type_and_id() -> None:
    correlation = _correlation("github_pr", "987654321")
    assert is_root_cause_match(correlation, _gold()) is True


def test_does_not_match_different_entity_id() -> None:
    correlation = _correlation("github_pr", "111111111")
    assert is_root_cause_match(correlation, _gold()) is False


def test_does_not_match_same_id_different_entity_type() -> None:
    """A PR id and a TCMR key happening to be the same string shouldn't collide."""
    correlation = _correlation("jira_tcmr", "987654321")
    assert is_root_cause_match(correlation, _gold()) is False


# ---------------------------------------------------------------------------
# classify_root_cause_matches
# ---------------------------------------------------------------------------


def test_classifies_matching_correlation_as_root_cause() -> None:
    match = _correlation("github_pr", "987654321")
    noise = _correlation("github_pr", "111111111")

    result = classify_root_cause_matches([match, noise], _gold())

    assert result.root_cause_matches == [match]
    assert result.unresolved == [noise]
    assert result.root_cause_covered is True


def test_no_gold_entity_means_everything_unresolved() -> None:
    correlations = [_correlation("github_pr", "987654321")]

    result = classify_root_cause_matches(correlations, None)

    assert result.root_cause_matches == []
    assert result.unresolved == correlations
    assert result.root_cause_covered is False


def test_no_matches_means_root_cause_not_covered() -> None:
    correlations = [_correlation("github_pr", "111111111")]

    result = classify_root_cause_matches(correlations, _gold())

    assert result.root_cause_matches == []
    assert result.unresolved == correlations
    assert result.root_cause_covered is False


def test_empty_correlations_list() -> None:
    result = classify_root_cause_matches([], _gold())

    assert result.root_cause_matches == []
    assert result.unresolved == []
    assert result.root_cause_covered is False


# ---------------------------------------------------------------------------
# classify_by_service_match
# ---------------------------------------------------------------------------


def test_classify_by_service_match_relevant_on_overlap() -> None:
    correlation = _correlation("github_pr", "111", services=["biztech_office_infra"])

    result = classify_by_service_match([correlation], {"biztech_office_infra"})

    assert result == [(correlation, "relevant")]


def test_classify_by_service_match_noise_on_no_overlap() -> None:
    correlation = _correlation("github_pr", "111", services=["biztech_claude"])

    result = classify_by_service_match([correlation], {"powergrid"})

    assert result == [(correlation, "noise")]


def test_classify_by_service_match_normalizes_punctuation() -> None:
    correlation = _correlation("github_pr", "111", services=["biztech-iac-networking"])

    result = classify_by_service_match([correlation], {"biztech_iac_networking"})

    assert result == [(correlation, "relevant")]


def test_classify_by_service_match_noise_when_correlation_has_no_services() -> None:
    correlation = _correlation("github_pr", "111", services=None)

    result = classify_by_service_match([correlation], {"powergrid"})

    assert result == [(correlation, "noise")]


def test_classify_by_service_match_noise_when_no_incident_services() -> None:
    correlation = _correlation("github_pr", "111", services=["biztech_claude"])

    result = classify_by_service_match([correlation], set())

    assert result == [(correlation, "noise")]


# ---------------------------------------------------------------------------
# build_correlation_rows
# ---------------------------------------------------------------------------


def test_build_correlation_rows_includes_root_cause_and_relevance() -> None:
    root_cause = _correlation(
        "github_pr", "987654321", services=["biztech_logging"], base_score=0.95
    )
    relevant = _correlation(
        "github_pr", "111", services=["biztech_logging"], base_score=0.4
    )
    noise = _correlation("jira_tcmr", "TCMR-1")

    result = build_correlation_rows(
        reference_id="INC-1234",
        time_bucket="investigation",
        root_cause_matches=[root_cause],
        relevance_pairs=[(relevant, "relevant"), (noise, "noise")],
    )

    assert result == [
        {
            "reference_id": "INC-1234",
            "time_bucket": "investigation",
            "source": "braintrust",
            "entity_type": "github_pr",
            "entity_id": "987654321",
            "services": "biztech_logging",
            "classification": "root_cause",
            "reasoning": "",
            "base_llm_score": "0.95",
        },
        {
            "reference_id": "INC-1234",
            "time_bucket": "investigation",
            "source": "braintrust",
            "entity_type": "github_pr",
            "entity_id": "111",
            "services": "biztech_logging",
            "classification": "relevant",
            "reasoning": "",
            "base_llm_score": "0.4",
        },
        {
            "reference_id": "INC-1234",
            "time_bucket": "investigation",
            "source": "braintrust",
            "entity_type": "jira_tcmr",
            "entity_id": "TCMR-1",
            "services": "",
            "classification": "noise",
            "reasoning": "",
            "base_llm_score": "",
        },
    ]


def test_build_correlation_rows_joins_multiple_services() -> None:
    correlation = _correlation(
        "github_pr", "111", services=["biztech_logging", "biztech_alerting"]
    )

    result = build_correlation_rows(
        reference_id="INC-1234",
        time_bucket="postmortem",
        root_cause_matches=[correlation],
        relevance_pairs=[],
    )

    assert result[0]["services"] == "biztech_logging, biztech_alerting"


def test_build_correlation_rows_empty_input() -> None:
    assert (
        build_correlation_rows(
            reference_id="INC-1234",
            time_bucket="postmortem",
            root_cause_matches=[],
            relevance_pairs=[],
        )
        == []
    )


def test_build_correlation_rows_postmortem_source_is_database() -> None:
    root_cause = _correlation("github_pr", "987654321")

    result = build_correlation_rows(
        reference_id="INC-1234",
        time_bucket="postmortem",
        root_cause_matches=[root_cause],
        relevance_pairs=[],
    )

    assert result[0]["source"] == "database"
