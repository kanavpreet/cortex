"""Unit tests for IncidentIOTracker model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.models.incidentio_tracker import IncidentIOTracker


class TestIncidentIOTracker:
    """Test suite for IncidentIOTracker SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating IncidentIOTracker with valid data."""
        tracker = IncidentIOTracker(
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="OK",
        )
        assert tracker.timestamp == datetime(2024, 1, 9, 10, 30)
        assert tracker.status == "OK"
        assert tracker.id == 1  # Default value for single-row pattern
        assert tracker.error_message is None  # Default value

    def test_with_error_message(self) -> None:
        """Test creating IncidentIOTracker with error message."""
        tracker = IncidentIOTracker(
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="ERROR",
            error_message="Failed to fetch incidents",
        )
        assert tracker.status == "ERROR"
        assert tracker.error_message == "Failed to fetch incidents"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IncidentIOTracker.model_validate({})
        error_str = str(exc_info.value)
        assert "timestamp" in error_str
        assert "status" in error_str

    def test_timestamp_parsing_from_string(self) -> None:
        """Test timestamp field parses ISO 8601 strings correctly."""
        tracker = IncidentIOTracker.model_validate(
            {
                "timestamp": "2024-01-09T10:30:00Z",
                "status": "OK",
            }
        )
        assert isinstance(tracker.timestamp, datetime)
        assert tracker.timestamp.tzinfo is None  # Naive UTC
        assert tracker.timestamp.year == 2024
        assert tracker.timestamp.month == 1
        assert tracker.timestamp.day == 9
        assert tracker.timestamp.hour == 10
        assert tracker.timestamp.minute == 30

    def test_timestamp_parsing_from_datetime(self) -> None:
        """Test timestamp field accepts datetime objects."""
        dt = datetime(2024, 1, 9, 10, 30, 0)
        tracker = IncidentIOTracker(
            timestamp=dt,
            status="OK",
        )
        assert tracker.timestamp == dt

    def test_timestamp_parsing_from_timezone_aware_datetime(self) -> None:
        """Test timestamp converts timezone-aware datetime to naive UTC."""
        tracker = IncidentIOTracker.model_validate(
            {
                "timestamp": datetime(2024, 1, 9, 10, 30, 0, tzinfo=UTC),
                "status": "OK",
            }
        )
        assert tracker.timestamp.tzinfo is None  # Naive UTC
        assert tracker.timestamp == datetime(2024, 1, 9, 10, 30, 0)

    def test_status_values(self) -> None:
        """Test different status values."""
        tracker1 = IncidentIOTracker(
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="OK",
        )
        assert tracker1.status == "OK"

        tracker2 = IncidentIOTracker(
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="ERROR",
            error_message="Something went wrong",
        )
        assert tracker2.status == "ERROR"

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        tracker = IncidentIOTracker(
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="OK",
        )
        assert tracker.id == 1  # Single-row pattern default
        assert tracker.error_message is None

    def test_custom_id(self) -> None:
        """Test that custom id can be set."""
        tracker = IncidentIOTracker(
            id=42,
            timestamp=datetime(2024, 1, 9, 10, 30),
            status="OK",
        )
        assert tracker.id == 42

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": 1,
            "timestamp": datetime(2024, 1, 9, 10, 30),
            "status": "OK",
            "error_message": None,
        }
        tracker = IncidentIOTracker.model_validate(data)
        assert tracker.id == 1
        assert tracker.status == "OK"
        assert tracker.error_message is None
