"""Unit tests for ReliabilityCorrelationGroup and FeedbackEntry models."""

from datetime import datetime

from common.models.reliability_correlation_group import (
    FeedbackEntry,
    ReliabilityCorrelationGroup,
)


class TestFeedbackEntry:
    """Test suite for FeedbackEntry model."""

    def test_create_feedback_entry(self) -> None:
        """Test creating a FeedbackEntry with all required fields."""
        ts = datetime(2025, 1, 15, 12, 0, 0)
        entry = FeedbackEntry(user_id="user-1", value="POSITIVE", timestamp=ts)
        assert entry.user_id == "user-1"
        assert entry.value == "POSITIVE"
        assert entry.timestamp == ts

    def test_model_dump(self) -> None:
        """Test serializing FeedbackEntry to dict."""
        ts = datetime(2025, 1, 15, 12, 0, 0)
        entry = FeedbackEntry(user_id="user-2", value="NEGATIVE", timestamp=ts)
        data = entry.model_dump()
        assert data["user_id"] == "user-2"
        assert data["value"] == "NEGATIVE"


class TestReliabilityCorrelationGroup:
    """Test suite for ReliabilityCorrelationGroup SQLModel."""

    def test_required_fields_only(self) -> None:
        """Test creating with only required fields."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-99",
            anchor_type="incident",
            correlation_timestamp=now,
        )
        assert group.anchor_entity_id == "INC-99"
        assert group.anchor_type == "incident"
        assert group.correlation_timestamp == now

    def test_default_values(self) -> None:
        """Test that optional fields default correctly."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-1",
            anchor_type="incident",
            correlation_timestamp=now,
        )
        assert group.id is None
        assert group.services is None
        assert group.start_time is None
        assert group.end_time is None
        assert group.base_score is None
        assert group.final_score is None
        assert group.scoring_version is None
        assert group.feedback_list is None
        assert group.feedback_state == "NEUTRAL"
        assert group.review_status is False
        assert group.last_reviewed_at is None

    def test_all_fields(self) -> None:
        """Test creating with all fields populated."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        earlier = datetime(2025, 1, 15, 6, 0, 0)
        later = datetime(2025, 1, 15, 18, 0, 0)
        group = ReliabilityCorrelationGroup(
            id=7,
            anchor_entity_id="INC-99",
            anchor_type="incident",
            services=["svc-a", "svc-b"],
            start_time=earlier,
            end_time=later,
            correlation_timestamp=now,
            base_score=0.75,
            final_score=0.80,
            scoring_version="incident_v1",
            feedback_list=[
                {
                    "user_id": "u1",
                    "value": "POSITIVE",
                    "timestamp": "2025-01-15T12:00:00",
                }
            ],
            feedback_state="POSITIVE",
            review_status=True,
            last_reviewed_at=now,
        )
        assert group.id == 7
        assert group.services == ["svc-a", "svc-b"]
        assert group.start_time == earlier
        assert group.end_time == later
        assert group.base_score == 0.75
        assert group.final_score == 0.80
        assert group.scoring_version == "incident_v1"
        assert group.feedback_list is not None
        assert len(group.feedback_list) == 1
        assert group.feedback_state == "POSITIVE"
        assert group.review_status is True
        assert group.last_reviewed_at == now

    def test_model_validate_from_dict(self) -> None:
        """Test creating model from dict."""
        data = {
            "anchor_entity_id": "INC-5",
            "anchor_type": "incident",
            "correlation_timestamp": "2025-01-15T12:00:00",
            "base_score": 0.6,
        }
        group = ReliabilityCorrelationGroup.model_validate(data)
        assert group.anchor_entity_id == "INC-5"
        assert group.base_score == 0.6

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-1",
            anchor_type="incident",
            correlation_timestamp=now,
        )
        data = group.model_dump()
        assert data["anchor_entity_id"] == "INC-1"
        assert data["feedback_state"] == "NEUTRAL"
        assert data["review_status"] is False

    def test_table_name(self) -> None:
        """Test that the SQLModel table name is set correctly."""
        assert (
            ReliabilityCorrelationGroup.__tablename__
            == "reliability_correlation_groups"
        )

    def test_feedback_state_default_neutral(self) -> None:
        """Test that feedback_state defaults to NEUTRAL."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-1",
            anchor_type="incident",
            correlation_timestamp=now,
        )
        assert group.feedback_state == "NEUTRAL"

    def test_review_status_default_false(self) -> None:
        """Test that review_status defaults to False."""
        now = datetime(2025, 1, 15, 12, 0, 0)
        group = ReliabilityCorrelationGroup(
            anchor_entity_id="INC-1",
            anchor_type="incident",
            correlation_timestamp=now,
        )
        assert group.review_status is False
