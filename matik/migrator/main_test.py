"""Tests for Migrator service entry point."""

import sys
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs


def _mock_config() -> MagicMock:
    """Create a mock config object for testing."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    return mock


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_no_args_defaults_to_none_command(self) -> None:
        """Test that no arguments results in command=None (defaults to upgrade)."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator"]):
            args = parse_args()
            assert args.command is None

    def test_upgrade_no_revision(self) -> None:
        """Test upgrade command without revision defaults to head."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "upgrade"]):
            args = parse_args()
            assert args.command == "upgrade"
            assert args.revision == "head"

    def test_upgrade_with_revision(self) -> None:
        """Test upgrade command with specific revision."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "upgrade", "abc123"]):
            args = parse_args()
            assert args.command == "upgrade"
            assert args.revision == "abc123"

    def test_downgrade_requires_revision(self) -> None:
        """Test downgrade command requires revision argument."""
        from migrator.main import parse_args

        with (
            patch("sys.argv", ["migrator", "downgrade"]),
            pytest.raises(SystemExit),
        ):
            parse_args()

    def test_downgrade_with_revision(self) -> None:
        """Test downgrade command with revision."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "downgrade", "-1"]):
            args = parse_args()
            assert args.command == "downgrade"
            assert args.revision == "-1"

    def test_downgrade_to_base(self) -> None:
        """Test downgrade command to base."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "downgrade", "base"]):
            args = parse_args()
            assert args.command == "downgrade"
            assert args.revision == "base"

    def test_current_command(self) -> None:
        """Test current command."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "current"]):
            args = parse_args()
            assert args.command == "current"

    def test_history_command(self) -> None:
        """Test history command."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "history"]):
            args = parse_args()
            assert args.command == "history"

    def test_stamp_requires_revision(self) -> None:
        """Test stamp command requires revision argument."""
        from migrator.main import parse_args

        with (
            patch("sys.argv", ["migrator", "stamp"]),
            pytest.raises(SystemExit),
        ):
            parse_args()

    def test_stamp_with_revision(self) -> None:
        """Test stamp command with revision."""
        from migrator.main import parse_args

        with patch("sys.argv", ["migrator", "stamp", "head"]):
            args = parse_args()
            assert args.command == "stamp"
            assert args.revision == "head"


class TestMainCommands:
    """Tests for main() command dispatch."""

    def test_main_upgrade_default(self) -> None:
        """Test that main() runs upgrade to head by default (no command)."""
        from migrator.main import main

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            events = [log["event"] for log in cap_logs]
            assert "upgrading database to revision" in events
            assert "upgrade completed successfully" in events
            mock_command.upgrade.assert_called_once()

    def test_main_upgrade_explicit(self) -> None:
        """Test that main() runs upgrade with explicit command."""
        from migrator.main import main

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator", "upgrade"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            events = [log["event"] for log in cap_logs]
            assert "upgrading database to revision" in events
            mock_command.upgrade.assert_called_once()

    def test_main_upgrade_specific_revision(self) -> None:
        """Test that main() runs upgrade to specific revision."""
        from migrator.main import main

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator", "upgrade", "abc123"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            events = [log["event"] for log in cap_logs]
            assert "upgrading database to revision" in events
            mock_command.upgrade.assert_called_once()
            # Check revision argument
            call_args = mock_command.upgrade.call_args
            assert call_args[0][1] == "abc123"

    def test_main_downgrade(self) -> None:
        """Test that main() runs downgrade."""
        from migrator.main import main

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator", "downgrade", "-1"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            events = [log["event"] for log in cap_logs]
            assert "downgrading database to revision" in events
            assert "downgrade completed successfully" in events
            mock_command.downgrade.assert_called_once()
            call_args = mock_command.downgrade.call_args
            assert call_args[0][1] == "-1"

    def test_main_downgrade_to_base(self) -> None:
        """Test that main() runs downgrade to base."""
        from migrator.main import main

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator", "downgrade", "base"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            events = [log["event"] for log in cap_logs]
            assert "downgrading database to revision" in events
            mock_command.downgrade.assert_called_once()
            call_args = mock_command.downgrade.call_args
            assert call_args[0][1] == "base"

    def test_main_current(self) -> None:
        """Test that main() runs current command."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "current"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            mock_command.current.assert_called_once()
            call_args = mock_command.current.call_args
            assert call_args[1]["verbose"] is True

    def test_main_history(self) -> None:
        """Test that main() runs history command."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "history"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            mock_command.history.assert_called_once()
            call_args = mock_command.history.call_args
            assert call_args[1]["verbose"] is True

    def test_main_stamp(self) -> None:
        """Test that main() runs stamp command."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "stamp", "head"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            mock_command.stamp.assert_called_once()
            call_args = mock_command.stamp.call_args
            assert call_args[0][1] == "head"

    def test_main_stamp_specific_revision(self) -> None:
        """Test that main() runs stamp with specific revision."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "stamp", "abc123"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
        ):
            mock_config.return_value = MagicMock()
            main()

            mock_command.stamp.assert_called_once()
            call_args = mock_command.stamp.call_args
            assert call_args[0][1] == "abc123"


class TestErrorHandling:
    """Tests for error handling."""

    def test_main_exits_on_upgrade_failure(self) -> None:
        """Test that main() exits with code 1 when upgrade fails."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "upgrade"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_config.return_value = MagicMock()
            mock_command.upgrade.side_effect = Exception("Database connection failed")
            main()

        assert exc_info.value.code == 1

    def test_main_exits_on_downgrade_failure(self) -> None:
        """Test that main() exits with code 1 when downgrade fails."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "downgrade", "-1"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_config.return_value = MagicMock()
            mock_command.downgrade.side_effect = Exception("Downgrade failed")
            main()

        assert exc_info.value.code == 1

    def test_main_exits_on_stamp_failure(self) -> None:
        """Test that main() exits with code 1 when stamp fails."""
        from migrator.main import main

        with (
            patch("sys.argv", ["migrator", "stamp", "head"]),
            patch("migrator.main.load_config", return_value=_mock_config()),
            patch("migrator.main.Config") as mock_config,
            patch("migrator.main.command") as mock_command,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_config.return_value = MagicMock()
            mock_command.stamp.side_effect = Exception("Stamp failed")
            main()

        assert exc_info.value.code == 1


class TestModuleEntryPoint:
    """Tests for module entry point."""

    def test_module_entry_point(self) -> None:
        """Test that the main block calls main() when executed."""
        import runpy

        # Remove modules from sys.modules to avoid warning
        for mod in list(sys.modules.keys()):
            if mod == "migrator" or mod.startswith("migrator."):
                del sys.modules[mod]

        with (
            capture_logs() as cap_logs,
            patch("sys.argv", ["migrator"]),
            patch("common.config.load_config", return_value=_mock_config()),
            patch("alembic.config.Config") as mock_config,
            patch("alembic.command.upgrade") as mock_upgrade,
        ):
            mock_config.return_value = MagicMock()
            runpy.run_module("migrator", run_name="main", alter_sys=True)

            events = [log["event"] for log in cap_logs]
            assert "upgrading database to revision" in events
            mock_upgrade.assert_called_once()
