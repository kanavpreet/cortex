"""Cron expression utilities."""

import re

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def duration_to_cron(duration: str) -> str:
    """
    Convert duration notation to cron expression format.

    Supports minutes (m) and hours (h) only.

    Examples:
        - "5m" → "*/5 * * * *" (every 5 minutes)
        - "2h" → "0 */2 * * *" (every 2 hours)
        - "500m" → "0 */8 * * *" (converts to 8 hours, logs warning if not exact)

    Limitations:
        - Minutes: 1-59 (values >= 60 will be converted to hours)
        - Hours: 1-23 (values >= 24 will raise an error)

    Args:
        duration: Duration string like "5m" or "2h"

    Returns:
        Cron expression string

    Raises:
        ValueError: If format is invalid, value is non-numeric,
                    or value is zero/negative
    """
    if not duration:
        raise ValueError("duration cannot be empty")

    if len(duration) < 2:
        raise ValueError(
            f"invalid duration format: {duration} (expected format: 5m or 2h)"
        )

    unit = duration[-1]
    value_str = duration[:-1]

    # Validate numeric value
    if not re.match(r"^\d+$", value_str):
        raise ValueError(f"invalid duration value: {value_str} (must be numeric)")

    value = int(value_str)

    if value <= 0:
        raise ValueError(f"duration value must be positive: {value}")

    if unit == "m":
        # Handle minutes
        if value < 60:
            return f"*/{value} * * * *"

        # Minutes >= 60: convert to hours
        hours = value // 60
        remainder = value % 60
        if remainder != 0:
            logger.warning(
                "duration %s (%d minutes) is not evenly divisible by 60, "
                "converting to %d hours (remainder %d minutes ignored)",
                duration,
                value,
                hours,
                remainder,
            )
        if hours >= 24:
            raise ValueError(
                f"duration {duration} converts to {hours} hours, "
                "which exceeds cron's 24-hour limit"
            )
        return f"0 */{hours} * * *"

    elif unit == "h":
        # Handle hours
        if value < 24:
            return f"0 */{value} * * *"

        # Hours >= 24: this is problematic for cron
        days = value // 24
        raise ValueError(
            f"duration {duration} ({value} hours) converts to {days}+ days, "
            "which exceeds cron's hourly scheduling limit"
        )

    else:
        raise ValueError(
            f"invalid duration unit: {unit} (must be 'm' for minutes or 'h' for hours)"
        )
