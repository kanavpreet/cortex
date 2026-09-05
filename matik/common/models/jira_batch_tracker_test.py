"""Unit tests for JiraBatchTracker model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.jira_batch_tracker import JiraBatchTracker


class TestJiraBatchTracker:
    """Test suite for JiraBatchTracker SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating JiraBatchTracker with valid data."""
        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0),
            window_days=14,
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.ticket_type == "tcmr"
        assert tracker.batch_start == datetime(2024, 1, 1, 0, 0)
        assert tracker.batch_end == datetime(2024, 1, 15, 0, 0)
        assert tracker.window_days == 14
        assert tracker.updated_at == datetime(2024, 1, 9, 10, 0)
        assert tracker.status is None  # Default
        assert tracker.error_message is None  # Default
        assert tracker.last_processed_at is None  # Default

    def test_with_status_ok(self) -> None:
        """Test creating JiraBatchTracker with OK status."""
        tracker = JiraBatchTracker(
            ticket_type="operational",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0),
            window_days=14,
            status="OK",
            last_processed_at=datetime(2024, 1, 9, 9, 0),
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.status == "OK"
        assert tracker.last_processed_at == datetime(2024, 1, 9, 9, 0)
        assert tracker.error_message is None

    def test_with_status_error(self) -> None:
        """Test creating JiraBatchTracker with ERROR status."""
        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0),
            window_days=14,
            status="ERROR",
            error_message="Failed to connect to JIRA API",
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.status == "ERROR"
        assert tracker.error_message == "Failed to connect to JIRA API"

    def test_with_status_processing(self) -> None:
        """Test creating JiraBatchTracker with PROCESSING status."""
        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0),
            window_days=14,
            status="PROCESSING",
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.status == "PROCESSING"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            JiraBatchTracker.model_validate({"ticket_type": "tcmr"})
        error_str = str(exc_info.value)
        assert "batch_start" in error_str
        assert "batch_end" in error_str
        assert "window_days" in error_str
        assert "updated_at" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp fields parse ISO 8601 strings correctly."""
        tracker = JiraBatchTracker.model_validate(
            {
                "ticket_type": "tcmr",
                "batch_start": "2024-01-01T00:00:00Z",
                "batch_end": "2024-01-15T00:00:00Z",
                "window_days": 14,
                "last_processed_at": "2024-01-09T09:00:00Z",
                "updated_at": "2024-01-09T10:30:00Z",
            }
        )
        assert isinstance(tracker.batch_start, datetime)
        assert tracker.batch_start.tzinfo is None  # Naive UTC
        assert tracker.batch_start == datetime(2024, 1, 1, 0, 0)
        assert tracker.batch_end == datetime(2024, 1, 15, 0, 0)
        assert tracker.last_processed_at == datetime(2024, 1, 9, 9, 0)
        assert tracker.updated_at == datetime(2024, 1, 9, 10, 30)

    def test_timestamp_parsing_from_datetime(self) -> None:
        """Test timestamp fields accept datetime objects."""
        batch_start = datetime(2024, 1, 1, 0, 0)
        batch_end = datetime(2024, 1, 15, 0, 0)
        updated_at = datetime(2024, 1, 9, 10, 0)

        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=batch_start,
            batch_end=batch_end,
            window_days=14,
            updated_at=updated_at,
        )
        assert tracker.batch_start == batch_start
        assert tracker.batch_end == batch_end
        assert tracker.updated_at == updated_at

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamps convert timezone-aware datetime to naive UTC."""
        tracker = JiraBatchTracker.model_validate(
            {
                "ticket_type": "tcmr",
                "batch_start": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "batch_end": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "window_days": 14,
                "last_processed_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "updated_at": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
            }
        )
        assert tracker.batch_start.tzinfo is None
        assert tracker.batch_end.tzinfo is None
        assert tracker.last_processed_at is not None
        assert tracker.last_processed_at.tzinfo is None
        assert tracker.updated_at.tzinfo is None

    def test_nullable_timestamps(self) -> None:
        """Test that nullable timestamp fields accept None."""
        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 15, 0, 0),
            window_days=14,
            last_processed_at=None,
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.last_processed_at is None

    def test_ticket_type_values(self) -> None:
        """Test different ticket_type values."""
        for ticket_type in ["tcmr", "operational", "alert"]:
            tracker = JiraBatchTracker(
                ticket_type=ticket_type,
                batch_start=datetime(2024, 1, 1, 0, 0),
                batch_end=datetime(2024, 1, 15, 0, 0),
                window_days=14,
                updated_at=datetime(2024, 1, 9, 10, 0),
            )
            assert tracker.ticket_type == ticket_type

    def test_window_days_value(self) -> None:
        """Test different window_days values."""
        tracker = JiraBatchTracker(
            ticket_type="tcmr",
            batch_start=datetime(2024, 1, 1, 0, 0),
            batch_end=datetime(2024, 1, 8, 0, 0),
            window_days=7,
            updated_at=datetime(2024, 1, 9, 10, 0),
        )
        assert tracker.window_days == 7

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "ticket_type": "tcmr",
            "batch_start": datetime(2024, 1, 1, 0, 0),
            "batch_end": datetime(2024, 1, 15, 0, 0),
            "window_days": 14,
            "status": "OK",
            "error_message": None,
            "last_processed_at": datetime(2024, 1, 9, 9, 0),
            "updated_at": datetime(2024, 1, 9, 10, 0),
        }
        tracker = JiraBatchTracker.model_validate(data)
        assert tracker.ticket_type == "tcmr"
        assert tracker.batch_start == datetime(2024, 1, 1, 0, 0)
        assert tracker.status == "OK"
        assert tracker.window_days == 14
