"""Unit tests for GHEPullRequest model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.ghe_pr import GHEPullRequest


class TestGHEPullRequest:
    """Test suite for GHEPullRequest SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating GHEPullRequest with valid data."""
        pr = GHEPullRequest(
            pull_request_id=1,
            pull_request_number=1,
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            merged=True,
            state="closed",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            target_branch_name="main",
        )
        assert pr.pull_request_id == 1
        assert pr.merged is True
        assert pr.state == "closed"
        assert pr.locked is False
        assert pr.created_at == datetime(2024, 1, 9, 10, 0)
        assert pr.org_id == 11
        assert pr.org_login == "airbnb"
        assert pr.repo_id == 123
        assert pr.repo_name == "my-service"
        assert pr.target_branch_name == "main"
        assert pr.closed_at is None  # Default
        assert pr.merged_at is None  # Default
        assert pr.pull_request_summary is None  # Default
        assert pr.jira_tcmr_key is None  # Default
        assert pr.environment is None  # Default
        assert pr.description_hash is None  # Default
        assert pr.deleted_at is None  # Default

    def test_with_all_optional_fields(self) -> None:
        """Test creating GHEPullRequest with all optional fields."""
        pr = GHEPullRequest(
            pull_request_id=1,
            pull_request_number=1,
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            merged=True,
            state="closed",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            closed_at=datetime(2024, 1, 10, 10, 0),
            merged_at=datetime(2024, 1, 10, 10, 0),
            target_branch_name="main",
            pull_request_summary="Added authentication feature",
            jira_tcmr_key="TCMR-456",
            environment="production",
            description_hash="abc123def456",
            deleted_at=datetime(2024, 1, 11, 10, 0),
        )
        assert pr.closed_at == datetime(2024, 1, 10, 10, 0)
        assert pr.merged_at == datetime(2024, 1, 10, 10, 0)
        assert pr.pull_request_summary == "Added authentication feature"
        assert pr.jira_tcmr_key == "TCMR-456"
        assert pr.environment == "production"
        assert pr.description_hash == "abc123def456"
        assert pr.deleted_at == datetime(2024, 1, 11, 10, 0)

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            GHEPullRequest.model_validate({"pull_request_id": 1})
        error_str = str(exc_info.value)
        assert "merged" in error_str
        assert "state" in error_str
        assert "locked" in error_str
        assert "created_at" in error_str
        assert "org_id" in error_str
        assert "org_login" in error_str
        assert "repo_id" in error_str
        assert "repo_name" in error_str
        assert "target_branch_name" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp fields parse ISO 8601 strings correctly."""
        pr = GHEPullRequest.model_validate(
            {
                "pull_request_id": 1,
                "pull_request_number": 1,
                "merged": False,
                "state": "open",
                "locked": False,
                "created_at": "2024-01-09T10:30:00Z",
                "closed_at": "2024-01-10T12:00:00Z",
                "merged_at": "2024-01-10T12:00:00Z",
                "org_id": 11,
                "org_login": "airbnb",
                "repo_id": 123,
                "repo_name": "my-service",
                "target_branch_name": "main",
                "deleted_at": "2024-01-11T14:00:00Z",
            }
        )
        assert isinstance(pr.created_at, datetime)
        assert pr.created_at.tzinfo is None  # Naive UTC
        assert pr.created_at == datetime(2024, 1, 9, 10, 30)
        assert pr.closed_at == datetime(2024, 1, 10, 12, 0)
        assert pr.merged_at == datetime(2024, 1, 10, 12, 0)
        assert pr.deleted_at == datetime(2024, 1, 11, 14, 0)

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamps convert timezone-aware datetime to naive UTC."""
        pr = GHEPullRequest.model_validate(
            {
                "pull_request_id": 1,
                "pull_request_number": 1,
                "merged": False,
                "state": "open",
                "locked": False,
                "created_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "org_id": 11,
                "org_login": "airbnb",
                "repo_id": 123,
                "repo_name": "my-service",
                "target_branch_name": "main",
            }
        )
        assert pr.created_at.tzinfo is None
        assert pr.created_at == datetime(2024, 1, 9, 10, 30, 0)

    def test_nullable_timestamps(self) -> None:
        """Test that nullable timestamp fields accept None."""
        pr = GHEPullRequest(
            pull_request_id=1,
            pull_request_number=1,
            merged=False,
            state="open",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            closed_at=None,
            merged_at=None,
            deleted_at=None,
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            target_branch_name="main",
        )
        assert pr.closed_at is None
        assert pr.merged_at is None
        assert pr.deleted_at is None

    def test_boolean_fields(self) -> None:
        """Test boolean fields (merged, locked) work correctly."""
        pr1 = GHEPullRequest(
            pull_request_id=1,
            pull_request_number=1,
            merged=True,
            state="closed",
            locked=True,
            created_at=datetime(2024, 1, 9, 10, 0),
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            target_branch_name="main",
        )
        assert pr1.merged is True
        assert pr1.locked is True

        pr2 = GHEPullRequest(
            pull_request_id=2,
            pull_request_number=2,
            merged=False,
            state="open",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            target_branch_name="main",
        )
        assert pr2.merged is False
        assert pr2.locked is False

    def test_state_values(self) -> None:
        """Test different state values."""
        pr1 = GHEPullRequest(
            pull_request_id=1,
            pull_request_number=1,
            merged=False,
            state="open",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            target_branch_name="main",
        )
        assert pr1.state == "open"

        pr2 = GHEPullRequest(
            pull_request_id=2,
            pull_request_number=2,
            merged=True,
            state="closed",
            locked=False,
            created_at=datetime(2024, 1, 9, 10, 0),
            org_id=11,
            org_login="airbnb",
            repo_id=123,
            repo_name="my-service",
            target_branch_name="main",
        )
        assert pr2.state == "closed"

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "pull_request_id": 123,
            "pull_request_number": 123,
            "org_id": 11,
            "org_login": "airbnb",
            "repo_id": 456,
            "repo_name": "my-service",
            "merged": True,
            "state": "closed",
            "locked": False,
            "created_at": datetime(2024, 1, 9, 10, 0),
            "closed_at": datetime(2024, 1, 10, 10, 0),
            "merged_at": datetime(2024, 1, 10, 10, 0),
            "target_branch_name": "main",
            "pull_request_summary": "Test summary",
            "jira_tcmr_key": "TCMR-789",
            "environment": "production",
            "description_hash": "test_hash_123",
            "deleted_at": None,
        }
        pr = GHEPullRequest.model_validate(data)
        assert pr.pull_request_id == 123
        assert pr.repo_id == 456
        assert pr.repo_name == "my-service"
        assert pr.merged is True
        assert pr.state == "closed"
        assert pr.environment == "production"
        assert pr.description_hash == "test_hash_123"
        assert pr.jira_tcmr_key == "TCMR-789"

    def test_table_schema(self) -> None:
        """Primary key, unique constraint, and index reflect the collapsed table."""
        from sqlalchemy import Index, UniqueConstraint

        table = GHEPullRequest.__table__  # type: ignore[attr-defined]
        assert table.name == "ghe_pull_requests"

        # Primary key is pull_request_id (no surrogate id column).
        pk_cols = {c.name for c in table.primary_key.columns}
        assert pk_cols == {"pull_request_id"}
        assert "id" not in table.columns
        assert "repository_id" not in table.columns
        # title was dropped (contained PII).
        assert "title" not in table.columns

        # Unique constraint over (org_id, repo_id, pull_request_number).
        uniques = {
            c.name: {col.name for col in c.columns}
            for c in table.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert "uq_ghe_pr_org_repo_number" in uniques
        assert uniques["uq_ghe_pr_org_repo_number"] == {
            "org_id",
            "repo_id",
            "pull_request_number",
        }

        # Index over (org_login, repo_name, pull_request_number).
        index_names = {ix.name for ix in table.indexes if isinstance(ix, Index)}
        assert "ix_ghe_pr_org_repo_number" in index_names
