"""Unit tests for cron utilities."""

import pytest

from common.utils.cron_utils import duration_to_cron


class TestDurationToCron:
    """Test suite for duration_to_cron function."""

    def test_minutes_simple(self) -> None:
        """Test simple minute durations."""
        assert duration_to_cron("5m") == "*/5 * * * *"
        assert duration_to_cron("1m") == "*/1 * * * *"
        assert duration_to_cron("30m") == "*/30 * * * *"
        assert duration_to_cron("59m") == "*/59 * * * *"

    def test_hours_simple(self) -> None:
        """Test simple hour durations."""
        assert duration_to_cron("1h") == "0 */1 * * *"
        assert duration_to_cron("2h") == "0 */2 * * *"
        assert duration_to_cron("12h") == "0 */12 * * *"
        assert duration_to_cron("23h") == "0 */23 * * *"

    def test_minutes_converted_to_hours(self) -> None:
        """Test minutes >= 60 are converted to hours."""
        assert duration_to_cron("60m") == "0 */1 * * *"
        assert duration_to_cron("120m") == "0 */2 * * *"
        assert duration_to_cron("180m") == "0 */3 * * *"

    def test_minutes_not_evenly_divisible_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test warning is logged when minutes don't divide evenly into hours."""
        import logging

        with caplog.at_level(logging.WARNING):
            result = duration_to_cron("500m")

        assert result == "0 */8 * * *"
        # Check that a warning was logged - structlog outputs through stdlib logging
        assert len(caplog.records) >= 1
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1
        # The message contains the format string with args rendered by structlog
        assert "not evenly divisible by 60" in caplog.text
        assert "remainder" in caplog.text and "minutes ignored" in caplog.text

    def test_empty_duration_raises_error(self) -> None:
        """Test empty duration raises error."""
        with pytest.raises(ValueError, match="duration cannot be empty"):
            duration_to_cron("")

    def test_too_short_duration_raises_error(self) -> None:
        """Test single character duration raises error."""
        with pytest.raises(ValueError, match="invalid duration format"):
            duration_to_cron("m")
        with pytest.raises(ValueError, match="invalid duration format"):
            duration_to_cron("5")

    def test_non_numeric_value_raises_error(self) -> None:
        """Test non-numeric value raises error."""
        with pytest.raises(ValueError, match="must be numeric"):
            duration_to_cron("abm")
        with pytest.raises(ValueError, match="must be numeric"):
            duration_to_cron("5.5m")
        with pytest.raises(ValueError, match="must be numeric"):
            duration_to_cron("-5m")

    def test_zero_value_raises_error(self) -> None:
        """Test zero value raises error."""
        with pytest.raises(ValueError, match="must be positive"):
            duration_to_cron("0m")
        with pytest.raises(ValueError, match="must be positive"):
            duration_to_cron("0h")

    def test_invalid_unit_raises_error(self) -> None:
        """Test invalid unit raises error."""
        with pytest.raises(ValueError, match="invalid duration unit"):
            duration_to_cron("5s")
        with pytest.raises(ValueError, match="invalid duration unit"):
            duration_to_cron("5d")
        with pytest.raises(ValueError, match="invalid duration unit"):
            duration_to_cron("5x")

    def test_hours_exceeds_24_raises_error(self) -> None:
        """Test hours >= 24 raises error."""
        with pytest.raises(ValueError, match="exceeds cron's hourly"):
            duration_to_cron("24h")
        with pytest.raises(ValueError, match="exceeds cron's hourly"):
            duration_to_cron("48h")

    def test_minutes_exceeds_24_hours_raises_error(self) -> None:
        """Test minutes converting to >= 24 hours raises error."""
        with pytest.raises(ValueError, match="exceeds cron's 24-hour limit"):
            duration_to_cron("1440m")  # 24 hours
        with pytest.raises(ValueError, match="exceeds cron's 24-hour limit"):
            duration_to_cron("2880m")  # 48 hours
