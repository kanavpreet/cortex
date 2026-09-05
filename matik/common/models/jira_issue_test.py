"""Unit tests for JIRA issue models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.jira_issue import (
    Comment,
    Comments,
    Issue,
    IssueFields,
    IssueLink,
    IssueLinkType,
    Parent,
    Resolution,
    Status,
)


class TestParent:
    """Test suite for Parent Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating Parent with valid data."""
        parent = Parent(id="12345", key="PROJ-100")
        assert parent.id == "12345"
        assert parent.key == "PROJ-100"

    def test_default_values(self) -> None:
        """Test that all fields have default None values."""
        parent = Parent()
        assert parent.id is None
        assert parent.key is None


class TestStatus:
    """Test suite for Status Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating Status with valid data."""
        status = Status(
            self="https://jira.example.com/status/1",
            id="1",
            name="Open",
        )
        assert status.self == "https://jira.example.com/status/1"
        assert status.id == "1"
        assert status.name == "Open"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Status(self="https://jira.example.com/status/1")
        error_str = str(exc_info.value)
        assert "id" in error_str
        assert "name" in error_str


class TestResolution:
    """Test suite for Resolution Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating Resolution with valid data."""
        resolution = Resolution(
            self="https://jira.example.com/resolution/1",
            id="1",
            name="Done",
        )
        assert resolution.self == "https://jira.example.com/resolution/1"
        assert resolution.id == "1"
        assert resolution.name == "Done"

    def test_different_resolution_names(self) -> None:
        """Test different resolution names."""
        for name in ["Done", "Fixed", "Won't Fix", "Duplicate"]:
            resolution = Resolution(
                self="https://jira.example.com/resolution/1",
                id="1",
                name=name,
            )
            assert resolution.name == name


class TestComment:
    """Test suite for Comment Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating Comment with valid data."""
        comment = Comment(
            id="10001",
            self="https://jira.example.com/comment/10001",
            name="author_name",
            body="This is a comment",
            created="2024-01-09T10:00:00Z",
            updated="2024-01-09T11:00:00Z",
        )
        assert comment.id == "10001"
        assert comment.body == "This is a comment"

    def test_default_values(self) -> None:
        """Test that all fields have default None values."""
        comment = Comment()
        assert comment.id is None
        assert comment.self is None
        assert comment.name is None
        assert comment.body is None
        assert comment.created is None
        assert comment.updated is None


class TestComments:
    """Test suite for Comments Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating Comments with valid data."""
        comments = Comments(
            comments=[
                Comment(id="1", body="First comment"),
                Comment(id="2", body="Second comment"),
            ]
        )
        assert comments.comments is not None
        assert len(comments.comments) == 2
        assert comments.comments[0].body == "First comment"

    def test_default_values(self) -> None:
        """Test default None value for comments."""
        comments = Comments()
        assert comments.comments is None


class TestIssueLinkType:
    """Test suite for IssueLinkType Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating IssueLinkType with valid data."""
        link_type = IssueLinkType(
            id="10001",
            self="https://jira.example.com/linktype/10001",
            name="Blocks",
            inward="is blocked by",
            outward="blocks",
        )
        assert link_type.id == "10001"
        assert link_type.name == "Blocks"
        assert link_type.inward == "is blocked by"
        assert link_type.outward == "blocks"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IssueLinkType(name="Blocks")
        error_str = str(exc_info.value)
        assert "inward" in error_str
        assert "outward" in error_str


class TestIssueLink:
    """Test suite for IssueLink Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating IssueLink with valid data."""
        link = IssueLink(
            id="10001",
            self="https://jira.example.com/link/10001",
            type=IssueLinkType(
                name="Blocks",
                inward="is blocked by",
                outward="blocks",
            ),
        )
        assert link.id == "10001"
        assert link.type.name == "Blocks"
        assert link.outward_issue is None
        assert link.inward_issue is None

    def test_with_outward_issue(self) -> None:
        """Test creating IssueLink with outward issue."""
        link = IssueLink(
            type=IssueLinkType(
                name="Relates",
                inward="relates to",
                outward="relates to",
            ),
            outward_issue=Issue(id="99999", key="PROJ-999"),
        )
        assert link.outward_issue is not None
        assert link.outward_issue.key == "PROJ-999"


class TestIssueFields:
    """Test suite for IssueFields Pydantic model."""

    def test_valid_instantiation_minimal(self) -> None:
        """Test creating IssueFields with minimal data."""
        fields = IssueFields()
        assert fields.summary is None
        assert fields.environment is None
        assert fields.status is None
        assert fields.created is None

    def test_with_all_fields(self) -> None:
        """Test creating IssueFields with all fields."""
        fields = IssueFields(
            summary="Fix login bug",
            environment="Production",
            status=Status(
                self="https://jira.example.com/status/1", id="1", name="In Progress"
            ),
            resolution=Resolution(
                self="https://jira.example.com/resolution/1", id="1", name="Done"
            ),
            created=datetime(2024, 1, 9, 10, 0),
            updated=datetime(2024, 1, 9, 11, 0),
            duedate=datetime(2024, 1, 15, 0, 0),
            resolutiondate=datetime(2024, 1, 10, 0, 0),
            labels=["bug", "critical"],
            parent=Parent(id="100", key="PROJ-100"),
            comments=Comments(comments=[Comment(id="1", body="Test")]),
        )
        assert fields.summary == "Fix login bug"
        assert fields.environment == "Production"
        assert fields.status is not None
        assert fields.status.name == "In Progress"
        assert fields.resolution is not None
        assert fields.resolution.name == "Done"
        assert fields.created == datetime(2024, 1, 9, 10, 0)
        assert fields.labels == ["bug", "critical"]
        assert fields.parent is not None
        assert fields.parent.key == "PROJ-100"

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp fields parse ISO 8601 strings correctly."""
        fields = IssueFields(
            created="2024-01-09T10:30:00Z",
            updated="2024-01-09T11:00:00Z",
            duedate="2024-01-15T00:00:00Z",
            resolutiondate="2024-01-10T14:00:00Z",
        )
        assert isinstance(fields.created, datetime)
        assert fields.created.tzinfo is None  # Naive UTC
        assert fields.created == datetime(2024, 1, 9, 10, 30)
        assert fields.updated == datetime(2024, 1, 9, 11, 0)
        assert fields.duedate == datetime(2024, 1, 15, 0, 0)
        assert fields.resolutiondate == datetime(2024, 1, 10, 14, 0)

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamps convert timezone-aware datetime to naive UTC."""
        dt = datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC)
        fields = IssueFields(created=dt, updated=dt)
        assert fields.created is not None
        assert fields.created.tzinfo is None
        assert fields.created == datetime(2024, 1, 9, 10, 30, 0)


class TestIssue:
    """Test suite for Issue Pydantic model."""

    def test_valid_instantiation_minimal(self) -> None:
        """Test creating Issue with minimal data."""
        issue = Issue()
        assert issue.id is None
        assert issue.self is None
        assert issue.key is None
        assert issue.fields is None
        assert issue.issue_summary is None
        assert issue.issue_comments_summary is None
        assert issue.summary_hash is None
        assert issue.comments_hash is None
        assert issue.tcmr_related_git_pr_link is None
        assert issue.tcmr_related_services is None
        assert issue.tcmr_planned_start_date is None
        assert issue.tcmr_planned_end_date is None

    def test_with_basic_fields(self) -> None:
        """Test creating Issue with basic fields."""
        issue = Issue(
            id="12345",
            self="https://jira.example.com/issue/12345",
            key="PROJ-123",
        )
        assert issue.id == "12345"
        assert issue.self == "https://jira.example.com/issue/12345"
        assert issue.key == "PROJ-123"

    def test_with_issue_fields(self) -> None:
        """Test creating Issue with IssueFields."""
        issue = Issue(
            id="12345",
            key="PROJ-123",
            fields=IssueFields(
                summary="Test issue",
                status=Status(
                    self="https://jira.example.com/status/1", id="1", name="Open"
                ),
            ),
        )
        assert issue.fields is not None
        assert issue.fields.summary == "Test issue"
        assert issue.fields.status is not None
        assert issue.fields.status.name == "Open"

    def test_with_llm_generated_fields(self) -> None:
        """Test creating Issue with LLM-generated fields."""
        issue = Issue(
            id="12345",
            key="PROJ-123",
            issue_summary="LLM summary of the issue",
            issue_comments_summary="LLM summary of comments",
        )
        assert issue.issue_summary == "LLM summary of the issue"
        assert issue.issue_comments_summary == "LLM summary of comments"

    def test_with_hash_fields(self) -> None:
        """Test creating Issue with hash fields for change detection."""
        # SHA256 produces 64-character hex strings
        summary_hash = "a" * 64
        comments_hash = "b" * 64

        issue = Issue(
            id="12345",
            key="PROJ-123",
            issue_summary="LLM summary",
            issue_comments_summary="LLM comments summary",
            summary_hash=summary_hash,
            comments_hash=comments_hash,
        )
        assert issue.summary_hash == summary_hash
        assert issue.comments_hash == comments_hash
        assert len(issue.summary_hash) == 64
        assert len(issue.comments_hash) == 64

    def test_with_tcmr_fields(self) -> None:
        """Test creating Issue with TCMR-specific fields."""
        issue = Issue(
            id="12345",
            key="TCMR-456",
            tcmr_related_git_pr_link="https://github.com/org/repo/pull/123",
            tcmr_related_services=["service-abc", "service-def"],
        )
        assert issue.tcmr_related_git_pr_link == "https://github.com/org/repo/pull/123"
        assert issue.tcmr_related_services == ["service-abc", "service-def"]

    def test_with_tcmr_planned_dates(self) -> None:
        """Test creating Issue with TCMR planned start and end dates."""
        issue = Issue(
            id="12345",
            key="TCMR-456",
            tcmr_planned_start_date=datetime(2024, 3, 1, 9, 0),
            tcmr_planned_end_date=datetime(2024, 3, 15, 17, 0),
        )
        assert issue.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert issue.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)

    def test_tcmr_planned_dates_parse_from_string(self) -> None:
        """Test that planned date fields parse ISO 8601 strings to naive UTC."""
        issue = Issue(
            id="12345",
            key="TCMR-456",
            tcmr_planned_start_date="2024-03-01T09:00:00Z",
            tcmr_planned_end_date="2024-03-15T17:00:00Z",
        )
        assert isinstance(issue.tcmr_planned_start_date, datetime)
        assert issue.tcmr_planned_start_date.tzinfo is None
        assert issue.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert isinstance(issue.tcmr_planned_end_date, datetime)
        assert issue.tcmr_planned_end_date.tzinfo is None
        assert issue.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)

    def test_tcmr_planned_dates_parse_from_timezone_aware_datetime(self) -> None:
        """Test that planned date fields convert timezone-aware datetime to naive UTC."""
        dt_start = datetime(2024, 3, 1, 9, 0, 0, tzinfo=UTC)
        dt_end = datetime(2024, 3, 15, 17, 0, 0, tzinfo=UTC)
        issue = Issue(
            id="12345",
            key="TCMR-456",
            tcmr_planned_start_date=dt_start,
            tcmr_planned_end_date=dt_end,
        )
        assert issue.tcmr_planned_start_date is not None
        assert issue.tcmr_planned_start_date.tzinfo is None
        assert issue.tcmr_planned_start_date == datetime(2024, 3, 1, 9, 0)
        assert issue.tcmr_planned_end_date is not None
        assert issue.tcmr_planned_end_date.tzinfo is None
        assert issue.tcmr_planned_end_date == datetime(2024, 3, 15, 17, 0)

    def test_tcmr_planned_dates_default_none(self) -> None:
        """Test that planned date fields default to None when not provided."""
        issue = Issue(id="12345", key="TCMR-456")
        assert issue.tcmr_planned_start_date is None
        assert issue.tcmr_planned_end_date is None

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": "12345",
            "self": "https://jira.example.com/issue/12345",
            "key": "PROJ-123",
            "fields": {
                "summary": "Test issue",
                "environment": "Production",
                "created": datetime(2024, 1, 9, 10, 0),
                "labels": ["test", "prod"],
            },
            "issue_summary": "AI summary",
            "tcmr_related_git_pr_link": "https://github.com/org/repo/pull/1",
        }
        issue = Issue.model_validate(data)
        assert issue.id == "12345"
        assert issue.key == "PROJ-123"
        assert issue.fields is not None
        assert issue.fields.summary == "Test issue"
        assert issue.issue_summary == "AI summary"
        assert issue.tcmr_related_git_pr_link == "https://github.com/org/repo/pull/1"
