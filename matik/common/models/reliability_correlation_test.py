"""Unit tests for ReliabilityCorrelation model."""

from datetime import datetime

from common.models.reliability_correlation import ReliabilityCorrelation


class TestReliabilityCorrelation:
    """Test suite for ReliabilityCorrelation SQLModel."""

    def test_required_fields_only(self) -> None:
        """Test creating with only required fields."""
        corr = ReliabilityCorrelation(
            anchor_entity_id="INC-99",
            correlation_type="SERVICE_MATCH",
            entity_type="github_pr",
            entity_id="PR-1",
        )
        assert corr.anchor_entity_id == "INC-99"
        assert corr.correlation_type == "SERVICE_MATCH"
        assert corr.entity_type == "github_pr"
        assert corr.entity_id == "PR-1"

    def test_default_values(self) -> None:
        """Test that optional fields default to None."""
        corr = ReliabilityCorrelation(
            anchor_entity_id="INC-1",
            correlation_type="LLM",
            entity_type="jira_tcmr",
            entity_id="TCMR-1",
        )
        assert corr.id is None
        assert corr.services is None
        assert corr.start_time is None
        assert corr.end_time is None
        assert corr.reasoning is None
        assert corr.base_score is None
        assert corr.final_score is None
        assert corr.scoring_version is None

    def test_all_fields(self) -> None:
        """Test creating with all fields populated."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        corr = ReliabilityCorrelation(
            id=42,
            anchor_entity_id="INC-99",
            correlation_type="SERVICE_MATCH",
            entity_type="github_pr",
            entity_id="PR-1",
            services=["svc-a", "svc-b"],
            start_time=now,
            end_time=now,
            reasoning="Matched by affected service.",
            base_score=0.85,
            final_score=0.68,
            scoring_version="incident_v1",
        )
        assert corr.id == 42
        assert corr.services == ["svc-a", "svc-b"]
        assert corr.start_time == now
        assert corr.end_time == now
        assert corr.reasoning == "Matched by affected service."
        assert corr.base_score == 0.85
        assert corr.final_score == 0.68
        assert corr.scoring_version == "incident_v1"

    def test_model_validate_from_dict(self) -> None:
        """Test creating model from dict."""
        data = {
            "anchor_entity_id": "INC-5",
            "correlation_type": "LLM",
            "entity_type": "jira_tcmr",
            "entity_id": "TCMR-10",
            "base_score": 0.7,
        }
        corr = ReliabilityCorrelation.model_validate(data)
        assert corr.anchor_entity_id == "INC-5"
        assert corr.base_score == 0.7

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        corr = ReliabilityCorrelation(
            anchor_entity_id="INC-1",
            correlation_type="LLM",
            entity_type="github_pr",
            entity_id="PR-1",
            final_score=0.5,
        )
        data = corr.model_dump()
        assert data["anchor_entity_id"] == "INC-1"
        assert data["final_score"] == 0.5
        assert data["services"] is None

    def test_table_name(self) -> None:
        """Test that the SQLModel table name is set correctly."""
        assert ReliabilityCorrelation.__tablename__ == "reliability_correlations"

    def test_services_accepts_list(self) -> None:
        """Test that services field accepts a list of strings."""
        corr = ReliabilityCorrelation(
            anchor_entity_id="INC-1",
            correlation_type="SERVICE_MATCH",
            entity_type="github_pr",
            entity_id="PR-1",
            services=["treehouse-api", "treehouse-web"],
        )
        assert corr.services == ["treehouse-api", "treehouse-web"]
