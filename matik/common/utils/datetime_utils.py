"""Common datetime utility functions"""

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    This is the standard way to get the current time for database storage
    in this codebase. All timestamps are stored as naive UTC datetimes.

    Returns:
        Current UTC time as a naive datetime (no timezone info).

    Example:
        >>> now = utc_now_naive()
        >>> now.tzinfo is None
        True
    """
    return datetime.now(UTC).replace(tzinfo=None)


def parse_date_string(date_str: str) -> datetime:
    """Parse a date string (YYYY-MM-DD) to a naive UTC datetime at midnight.

    Args:
        date_str: Date string in YYYY-MM-DD format

    Returns:
        Naive datetime representing midnight UTC on that date

    Example:
        >>> parse_date_string("2024-01-09")
        datetime.datetime(2024, 1, 9, 0, 0)
    """
    # Parse date and set to UTC, then strip timezone for naive UTC storage
    return (
        datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC).replace(tzinfo=None)
    )


def parse_timestamp_to_utc(v: str | datetime | None) -> datetime | None:
    """
    Parse and normalize timestamps to naive UTC.

    Accepts ISO 8601 strings or datetime objects and converts them to
    naive UTC datetime for consistent storage. Handles both 'Z' suffix
    and explicit timezone offsets.

    Args:
        v: Timestamp as string, datetime, or None

    Returns:
        Naive UTC datetime or None

    Raises:
        ValueError: If timestamp string cannot be parsed

    Examples:
        >>> parse_timestamp_to_utc("2024-01-09T12:00:00Z")
        datetime.datetime(2024, 1, 9, 12, 0, 0)

        >>> parse_timestamp_to_utc(None)
        None
    """
    if v is None:
        return None

    # Convert string to datetime
    dt: datetime
    dt = datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v

    # If timezone-aware, convert to UTC explicitly before stripping timezone
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)

    return dt
