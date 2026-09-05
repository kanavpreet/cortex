"""Unit tests for GHEOrgCrawlTracker model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.ghe_org_crawl_tracker import GHEOrgCrawlTracker


class TestGHEOrgCrawlTracker:
    """Test suite for GHEOrgCrawlTracker SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating GHEOrgCrawlTracker with valid data."""
        tracker = GHEOrgCrawlTracker(
            org_id=123,
            last_crawled_at=datetime(2024, 1, 1, 0, 0),
            created_at=datetime(2024, 1, 9, 10, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.org_id == 123
        assert tracker.last_crawled_at == datetime(2024, 1, 1, 0, 0)
        assert tracker.id is None  # Default for auto-increment PK

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            GHEOrgCrawlTracker.model_validate({})
        error_str = str(exc_info.value)
        assert "org_id" in error_str
        assert "last_crawled_at" in error_str

    def test_parses_iso_string_timestamp(self) -> None:
        """Test that ISO string timestamps are parsed to naive UTC."""
        tracker = GHEOrgCrawlTracker.model_validate(
            {
                "org_id": 1,
                "last_crawled_at": "2024-03-20T14:30:00Z",
                "created_at": "2024-03-20T14:30:00Z",
                "updated_at": "2024-03-20T14:30:00Z",
            }
        )
        assert tracker.last_crawled_at == datetime(2024, 3, 20, 14, 30, 0)
        assert tracker.last_crawled_at.tzinfo is None

    def test_converts_aware_timestamp_to_naive_utc(self) -> None:
        """Test that timezone-aware datetimes are normalized to naive UTC."""
        aware = datetime(2024, 3, 20, 14, 30, 0, tzinfo=UTC)
        tracker = GHEOrgCrawlTracker.model_validate(
            {
                "org_id": 1,
                "last_crawled_at": aware,
                "created_at": aware,
                "updated_at": aware,
            }
        )
        assert tracker.last_crawled_at == datetime(2024, 3, 20, 14, 30, 0)
        assert tracker.last_crawled_at.tzinfo is None
