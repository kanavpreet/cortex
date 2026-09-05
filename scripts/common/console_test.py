"""Unit tests for console utilities."""

from unittest.mock import patch

from .console import (
    COLOR_BLUE,
    COLOR_BOLD,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_RESET,
    COLOR_YELLOW,
    print_error,
    print_info,
    print_success,
    print_warning,
)


class TestColorConstants:
    """Test suite for ANSI color constants."""

    def test_color_reset(self) -> None:
        """Test COLOR_RESET constant."""
        assert COLOR_RESET == "\033[0m"

    def test_color_red(self) -> None:
        """Test COLOR_RED constant."""
        assert COLOR_RED == "\033[91m"

    def test_color_green(self) -> None:
        """Test COLOR_GREEN constant."""
        assert COLOR_GREEN == "\033[92m"

    def test_color_yellow(self) -> None:
        """Test COLOR_YELLOW constant."""
        assert COLOR_YELLOW == "\033[93m"

    def test_color_blue(self) -> None:
        """Test COLOR_BLUE constant."""
        assert COLOR_BLUE == "\033[94m"

    def test_color_bold(self) -> None:
        """Test COLOR_BOLD constant."""
        assert COLOR_BOLD == "\033[1m"


class TestPrintFunctions:
    """Test suite for print functions."""

    def test_print_error(self) -> None:
        """Test print_error function."""
        with patch("builtins.print") as mock_print:
            print_error("test error")
            mock_print.assert_called_once_with(f"{COLOR_RED}✗ test error{COLOR_RESET}")

    def test_print_success(self) -> None:
        """Test print_success function."""
        with patch("builtins.print") as mock_print:
            print_success("test success")
            mock_print.assert_called_once_with(
                f"{COLOR_GREEN}✓ test success{COLOR_RESET}"
            )

    def test_print_warning(self) -> None:
        """Test print_warning function."""
        with patch("builtins.print") as mock_print:
            print_warning("test warning")
            mock_print.assert_called_once_with(
                f"{COLOR_YELLOW}⚠ test warning{COLOR_RESET}"
            )

    def test_print_info(self) -> None:
        """Test print_info function."""
        with patch("builtins.print") as mock_print:
            print_info("test info")
            mock_print.assert_called_once_with(f"{COLOR_BLUE}ℹ test info{COLOR_RESET}")

    def test_print_error_with_empty_string(self) -> None:
        """Test print_error with empty string."""
        with patch("builtins.print") as mock_print:
            print_error("")
            mock_print.assert_called_once_with(f"{COLOR_RED}✗ {COLOR_RESET}")

    def test_print_success_with_empty_string(self) -> None:
        """Test print_success with empty string."""
        with patch("builtins.print") as mock_print:
            print_success("")
            mock_print.assert_called_once_with(f"{COLOR_GREEN}✓ {COLOR_RESET}")

    def test_print_warning_with_empty_string(self) -> None:
        """Test print_warning with empty string."""
        with patch("builtins.print") as mock_print:
            print_warning("")
            mock_print.assert_called_once_with(f"{COLOR_YELLOW}⚠ {COLOR_RESET}")

    def test_print_info_with_empty_string(self) -> None:
        """Test print_info with empty string."""
        with patch("builtins.print") as mock_print:
            print_info("")
            mock_print.assert_called_once_with(f"{COLOR_BLUE}ℹ {COLOR_RESET}")
