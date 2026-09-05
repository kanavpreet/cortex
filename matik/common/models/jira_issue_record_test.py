"""Unit tests for JIRA issue record models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.jira_issue_record import (
    JiraIssueRecord,
)


class TestJiraIssueRecord:
    """Test suite for JiraIssueRecord SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating JiraIssueRecord with valid data."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="PROJ-123",
            ticket_type="tcmr",
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.issue_id == "12345"
        assert record.issue_key == "PROJ-123"
        assert record.ticket_type == "tcmr"
        assert record.created_at == datetime(2024, 1, 9, 10, 0)
        assert record.id is None  # Default for auto-increment PK
        assert record.summary is None  # Default
        assert record.status_name is None  # Default
        assert record.issue_summary is None  # Default
        assert record.issue_comments_summary is None  # Default
        assert record.summary_hash is None  # Default
        assert record.comments_hash is None  # Default

    def test_with_optional_fields(self) -> None:
        """Test creating JiraIssueRecord with optional fields."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="PROJ-123",
            ticket_type="operational",
            summary="Fix bug in login flow",
            status_name="In Progress",
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.summary == "Fix bug in login flow"
        assert record.status_name == "In Progress"

    def test_with_llm_summary_fields(self) -> None:
        """Test creating JiraIssueRecord with LLM-generated summary fields."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="PROJ-123",
            ticket_type="tcmr",
            summary="Original JIRA summary",
            issue_summary="LLM-generated summary of the issue",
            issue_comments_summary="LLM-generated summary of comments",
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.summary == "Original JIRA summary"
        assert record.issue_summary == "LLM-generated summary of the issue"
        assert record.issue_comments_summary == "LLM-generated summary of comments"

    def test_with_hash_fields(self) -> None:
        """Test creating JiraIssueRecord with hash fields for change detection."""
        # SHA256 produces a 64-character hex string
        summary_hash = "a" * 64
        comments_hash = "b" * 64

        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="PROJ-123",
            ticket_type="tcmr",
            summary_hash=summary_hash,
            comments_hash=comments_hash,
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.summary_hash == summary_hash
        assert record.comments_hash == comments_hash
        assert len(record.summary_hash) == 64
        assert len(record.comments_hash) == 64

    def test_with_all_new_fields(self) -> None:
        """Test creating JiraIssueRecord with all LLM and hash fields."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="PROJ-123",
            ticket_type="operational",
            summary="Fix authentication timeout",
            status_name="Done",
            issue_summary="This issue addresses authentication timeouts in the login flow",
            issue_comments_summary="Team discussed root cause and implemented fix",
            summary_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            comments_hash="d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592",
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.issue_summary is not None
        assert record.issue_comments_summary is not None
        assert record.summary_hash is not None
        assert record.comments_hash is not None

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            JiraIssueRecord.model_validate({"issue_id": "12345"})
        error_str = str(exc_info.value)
        assert "issue_key" in error_str
        assert "ticket_type" in error_str
        assert "created_at" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test created_at field parses ISO 8601 strings correctly."""
        record = JiraIssueRecord.model_validate(
            {
                "issue_id": "12345",
                "issue_key": "PROJ-123",
                "ticket_type": "tcmr",
                "created_at": "2024-01-09T10:30:00Z",
            }
        )
        assert isinstance(record.created_at, datetime)
        assert record.created_at.tzinfo is None  # Naive UTC
        assert record.created_at == datetime(2024, 1, 9, 10, 30)

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test created_at converts timezone-aware datetime to naive UTC."""
        record = JiraIssueRecord.model_validate(
            {
                "issue_id": "12345",
                "issue_key": "PROJ-123",
                "ticket_type": "tcmr",
                "created_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
            }
        )
        assert record.created_at.tzinfo is None
        assert record.created_at == datetime(2024, 1, 9, 10, 30, 0)

    def test_ticket_type_values(self) -> None:
        """Test different ticket_type values."""
        for ticket_type in ["tcmr", "operational", "product"]:
            record = JiraIssueRecord(
                issue_id="12345",
                issue_key="PROJ-123",
                ticket_type=ticket_type,
                created_at=datetime(2024, 1, 9, 10, 0),
            )
            assert record.ticket_type == ticket_type

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "PROJ-123",
            "ticket_type": "tcmr",
            "summary": "Test issue",
            "status_name": "Done",
            "created_at": datetime(2024, 1, 9, 10, 0),
        }
        record = JiraIssueRecord.model_validate(data)
        assert record.id == 1
        assert record.issue_id == "12345"
        assert record.summary == "Test issue"

    def test_orm_mode_with_hash_fields(self) -> None:
        """Test that model can be created from dict-like objects with hash fields."""
        data = {
            "id": 1,
            "issue_id": "12345",
            "issue_key": "PROJ-123",
            "ticket_type": "tcmr",
            "summary": "Test issue",
            "status_name": "Done",
            "issue_summary": "LLM summary",
            "issue_comments_summary": "LLM comments summary",
            "summary_hash": "abc123" + "0" * 58,
            "comments_hash": "def456" + "0" * 58,
            "created_at": datetime(2024, 1, 9, 10, 0),
        }
        record = JiraIssueRecord.model_validate(data)
        assert record.id == 1
        assert record.issue_summary == "LLM summary"
        assert record.issue_comments_summary == "LLM comments summary"
        assert record.summary_hash == "abc123" + "0" * 58
        assert record.comments_hash == "def456" + "0" * 58

    def test_planned_dates_default_none(self) -> None:
        """Test that planned date fields default to None."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            created_at=datetime(2024, 1, 9, 10, 0),
        )
        assert record.tcmr_planned_start_date is None
        assert record.tcmr_planned_end_date is None

    def test_with_planned_dates(self) -> None:
        """Test creating JiraIssueRecord with planned start and end dates."""
        record = JiraIssueRecord(
            issue_id="12345",
            issue_key="TCMR-123",
            ticket_type="tcmr",
            created_at=datetime(2024, 1, 9, 10, 0),
            tcmr_planned_start_date=datetime(2024, 3, 1, 9, 0),
            tcmr_planned_end_date=datetime(2024, 3, 15, 17, 0),
        )
        assert record.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert record.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)

    def test_planned_dates_parse_from_string(self) -> None:
        """Test that planned date fields parse ISO 8601 strings to naive UTC."""
        record = JiraIssueRecord.model_validate(
            {
                "issue_id": "12345",
                "issue_key": "TCMR-123",
                "ticket_type": "tcmr",
                "created_at": "2024-01-09T10:30:00Z",
                "tcmr_planned_start_date": "2024-03-01T09:00:00Z",
                "tcmr_planned_end_date": "2024-03-15T17:00:00Z",
            }
        )
        assert isinstance(record.tcmr_planned_start_date, datetime)
        assert record.tcmr_planned_start_date.tzinfo is None
        assert record.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert isinstance(record.tcmr_planned_end_date, datetime)
        assert record.tcmr_planned_end_date.tzinfo is None
        assert record.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)

    def test_planned_dates_parse_from_timezone_aware_datetime(self) -> None:
        """Test that planned date fields convert timezone-aware datetime to naive UTC."""
        record = JiraIssueRecord.model_validate(
            {
                "issue_id": "12345",
                "issue_key": "TCMR-123",
                "ticket_type": "tcmr",
                "created_at": datetime(2024, 1, 9, 10, 0),
                "tcmr_planned_start_date": datetime(2024, 3, 1, 9, 0, tzinfo=UTC),
                "tcmr_planned_end_date": datetime(2024, 3, 15, 17, 0, tzinfo=UTC),
            }
        )
        assert record.tcmr_planned_start_date is not None
        assert record.tcmr_planned_start_date.tzinfo is None
        assert record.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert record.tcmr_planned_end_date is not None
        assert record.tcmr_planned_end_date.tzinfo is None
        assert record.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)
