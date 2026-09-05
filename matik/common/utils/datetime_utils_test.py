"""Unit tests for datetime_utils."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from common.utils.datetime_utils import (
    parse_date_string,
    parse_timestamp_to_utc,
    utc_now_naive,
)


class TestParseTimestampToUtc:
    """Test suite for parse_timestamp_to_utc function."""

    def test_none_input(self) -> None:
        """Test that None input returns None."""
        result = parse_timestamp_to_utc(None)
        assert result is None

    def test_string_with_z_suffix(self) -> None:
        """Test parsing ISO 8601 string with Z suffix."""
        result = parse_timestamp_to_utc("2024-01-09T12:00:00Z")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_string_with_utc_offset(self) -> None:
        """Test parsing ISO 8601 string with +00:00 UTC offset."""
        result = parse_timestamp_to_utc("2024-01-09T12:00:00+00:00")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_string_with_positive_offset(self) -> None:
        """Test parsing ISO 8601 string with positive timezone offset."""
        # 17:30 in +05:30 timezone = 12:00 UTC
        result = parse_timestamp_to_utc("2024-01-09T17:30:00+05:30")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_string_with_negative_offset(self) -> None:
        """Test parsing ISO 8601 string with negative timezone offset."""
        # 04:00 in -08:00 timezone = 12:00 UTC
        result = parse_timestamp_to_utc("2024-01-09T04:00:00-08:00")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_string_without_timezone(self) -> None:
        """Test parsing ISO 8601 string without timezone info."""
        result = parse_timestamp_to_utc("2024-01-09T12:00:00")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive datetime unchanged

    def test_naive_datetime(self) -> None:
        """Test that naive datetime is returned as-is."""
        dt = datetime(2024, 1, 9, 12, 0, 0)
        result = parse_timestamp_to_utc(dt)
        assert result is not None
        assert result == dt
        assert result.tzinfo is None

    def test_timezone_aware_utc_datetime(self) -> None:
        """Test that timezone-aware UTC datetime becomes naive UTC."""
        dt = datetime(2024, 1, 9, 12, 0, 0, tzinfo=UTC)
        result = parse_timestamp_to_utc(dt)
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_timezone_aware_non_utc_datetime(self) -> None:
        """Test that timezone-aware non-UTC datetime is converted to naive UTC."""
        # Create a timezone +05:30 (India)
        india_tz = timezone(timedelta(hours=5, minutes=30))
        # 17:30 in India = 12:00 UTC
        dt = datetime(2024, 1, 9, 17, 30, 0, tzinfo=india_tz)
        result = parse_timestamp_to_utc(dt)
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_invalid_string_raises_valueerror(self) -> None:
        """Test that invalid timestamp string raises ValueError."""
        with pytest.raises(ValueError):
            parse_timestamp_to_utc("not-a-timestamp")

    def test_microseconds_preserved(self) -> None:
        """Test that microseconds are preserved in conversion."""
        result = parse_timestamp_to_utc("2024-01-09T12:00:00.123456Z")
        assert result is not None
        assert result == datetime(2024, 1, 9, 12, 0, 0, 123456)
        assert result.tzinfo is None

    def test_date_boundary_conversion(self) -> None:
        """Test timezone conversion that crosses date boundary."""
        # 01:00 in +05:00 timezone = 20:00 previous day UTC
        result = parse_timestamp_to_utc("2024-01-10T01:00:00+05:00")
        assert result is not None
        assert result == datetime(2024, 1, 9, 20, 0, 0)
        assert result.tzinfo is None


class TestUtcNowNaive:
    """Test suite for utc_now_naive function."""

    def test_returns_naive_datetime(self) -> None:
        """Test that utc_now_naive returns a naive datetime (no tzinfo)."""
        result = utc_now_naive()
        assert result.tzinfo is None

    def test_returns_current_time(self) -> None:
        """Test that utc_now_naive returns approximately current time."""
        before = datetime.now(UTC).replace(tzinfo=None)
        result = utc_now_naive()
        after = datetime.now(UTC).replace(tzinfo=None)

        # Result should be between before and after
        assert before <= result <= after

    def test_returns_datetime_type(self) -> None:
        """Test that utc_now_naive returns a datetime instance."""
        result = utc_now_naive()
        assert isinstance(result, datetime)

    def test_multiple_calls_return_different_times(self) -> None:
        """Test that multiple calls return increasing times."""
        result1 = utc_now_naive()
        # Small delay to ensure time difference
        import time

        time.sleep(0.001)
        result2 = utc_now_naive()
        assert result2 >= result1


class TestParseDateString:
    """Test suite for parse_date_string function."""

    def test_valid_date_string(self) -> None:
        """Test parsing a valid YYYY-MM-DD date string."""
        result = parse_date_string("2024-01-09")
        assert result == datetime(2024, 1, 9, 0, 0, 0)
        assert result.tzinfo is None  # Naive UTC

    def test_returns_midnight(self) -> None:
        """Test that parsed date is at midnight (00:00:00)."""
        result = parse_date_string("2024-06-15")
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_returns_datetime_type(self) -> None:
        """Test that parse_date_string returns a datetime instance."""
        result = parse_date_string("2024-12-31")
        assert isinstance(result, datetime)

    def test_returns_naive_datetime(self) -> None:
        """Test that result is naive (no tzinfo)."""
        result = parse_date_string("2024-01-01")
        assert result.tzinfo is None

    def test_first_day_of_year(self) -> None:
        """Test parsing first day of year."""
        result = parse_date_string("2024-01-01")
        assert result == datetime(2024, 1, 1, 0, 0, 0)

    def test_last_day_of_year(self) -> None:
        """Test parsing last day of year."""
        result = parse_date_string("2024-12-31")
        assert result == datetime(2024, 12, 31, 0, 0, 0)

    def test_leap_year_date(self) -> None:
        """Test parsing Feb 29 in a leap year."""
        result = parse_date_string("2024-02-29")
        assert result == datetime(2024, 2, 29, 0, 0, 0)

    def test_invalid_date_format_raises_valueerror(self) -> None:
        """Test that invalid date format raises ValueError."""
        with pytest.raises(ValueError):
            parse_date_string("01-09-2024")  # Wrong format

    def test_invalid_date_string_raises_valueerror(self) -> None:
        """Test that invalid date string raises ValueError."""
        with pytest.raises(ValueError):
            parse_date_string("not-a-date")

    def test_invalid_date_values_raises_valueerror(self) -> None:
        """Test that invalid date values raise ValueError."""
        with pytest.raises(ValueError):
            parse_date_string("2024-13-01")  # Invalid month

    def test_non_leap_year_feb29_raises_valueerror(self) -> None:
        """Test that Feb 29 in non-leap year raises ValueError."""
        with pytest.raises(ValueError):
            parse_date_string("2023-02-29")  # 2023 is not a leap year
