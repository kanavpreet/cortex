"""Unit tests for GHEPRTracker model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.ghe_pr_tracker import GHEPRTracker


class TestGHEPRTracker:
    """Test suite for GHEPRTracker SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating GHEPRTracker with valid data."""
        tracker = GHEPRTracker(
            org_id=123,
            repo_id=456,
            cutoff_date=datetime(2024, 1, 1, 0, 0),
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.org_id == 123
        assert tracker.repo_id == 456
        assert tracker.cutoff_date == datetime(2024, 1, 1, 0, 0)
        assert tracker.created_at == datetime(2024, 1, 9, 10, 0)
        assert tracker.updated_at == datetime(2024, 1, 9, 10, 0)
        assert tracker.id is None  # Default for auto-increment PK
        assert tracker.prs_crawled_count == 0  # Default

    def test_with_custom_prs_crawled_count(self) -> None:
        """Test creating GHEPRTracker with custom prs_crawled_count."""
        tracker = GHEPRTracker(
            org_id=123,
            repo_id=456,
            cutoff_date=datetime(2024, 1, 1, 0, 0),
            prs_crawled_count=42,
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.prs_crawled_count == 42

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            GHEPRTracker.model_validate({"org_id": 123})
        error_str = str(exc_info.value)
        assert "repo_id" in error_str
        assert "cutoff_date" in error_str
        assert "created_at" in error_str
        assert "updated_at" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp fields parse ISO 8601 strings correctly."""
        tracker = GHEPRTracker.model_validate(
            {
                "org_id": 123,
                "repo_id": 456,
                "cutoff_date": "2024-01-01T00:00:00Z",
                "created_at": "2024-01-09T10:30:00Z",
                "updated_at": "2024-01-09T10:30:00Z",
            }
        )
        assert isinstance(tracker.cutoff_date, datetime)
        assert tracker.cutoff_date.tzinfo is None  # Naive UTC
        assert tracker.cutoff_date == datetime(2024, 1, 1, 0, 0)
        assert tracker.created_at == datetime(2024, 1, 9, 10, 30)
        assert tracker.updated_at == datetime(2024, 1, 9, 10, 30)

    def test_timestamp_parsing_from_datetime(self) -> None:
        """Test timestamp fields accept datetime objects."""
        cutoff = datetime(2024, 1, 1, 0, 0)
        created = datetime(2024, 1, 9, 10, 0)
        updated = datetime(2024, 1, 9, 11, 0)

        tracker = GHEPRTracker(
            org_id=123,
            repo_id=456,
            cutoff_date=cutoff,
            created_at=created,
            updated_at=updated,
        )
        assert tracker.cutoff_date == cutoff
        assert tracker.created_at == created
        assert tracker.updated_at == updated

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamps convert timezone-aware datetime to naive UTC."""
        tracker = GHEPRTracker.model_validate(
            {
                "org_id": 123,
                "repo_id": 456,
                "cutoff_date": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "created_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
            }
        )
        assert tracker.cutoff_date.tzinfo is None
        assert tracker.created_at.tzinfo is None
        assert tracker.updated_at.tzinfo is None
        assert tracker.cutoff_date == datetime(2024, 1, 9, 10, 30, 0)

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        tracker = GHEPRTracker(
            org_id=123,
            repo_id=456,
            cutoff_date=datetime(2024, 1, 1, 0, 0),
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.id is None  # Auto-increment PK defaults to None
        assert tracker.prs_crawled_count == 0

    def test_custom_id(self) -> None:
        """Test that custom id can be set."""
        tracker = GHEPRTracker(
            id=42,
            org_id=123,
            repo_id=456,
            cutoff_date=datetime(2024, 1, 1, 0, 0),
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.id == 42

    def test_foreign_key_relationships(self) -> None:
        """Test foreign key field values."""
        tracker = GHEPRTracker(
            org_id=100,
            repo_id=200,
            cutoff_date=datetime(2024, 1, 1, 0, 0),
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.org_id == 100
        assert tracker.repo_id == 200

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": 1,
            "org_id": 123,
            "repo_id": 456,
            "cutoff_date": datetime(2024, 1, 1, 0, 0),
            "prs_crawled_count": 25,
            "created_at": datetime(2024, 1, 9, 10, 0),
            "updated_at": datetime(2024, 1, 9, 11, 0),
        }
        tracker = GHEPRTracker.model_validate(data)
        assert tracker.id == 1
        assert tracker.org_id == 123
        assert tracker.repo_id == 456
        assert tracker.prs_crawled_count == 25
