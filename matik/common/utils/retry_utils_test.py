"""Unit tests for retry utilities."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import (
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)

from common.utils.retry_utils import (
    execute_with_retry,
    fetch_all_with_retry,
    get_backoff_delay,
    is_transient_db_error,
    run_with_retry,
)


class _FakeDBAPIError(Exception):
    """Stand-in for a DBAPI driver error carrying a MySQL (code, message)."""


def _operational_error(code: int) -> OperationalError:
    """Build a SQLAlchemy OperationalError wrapping a MySQL error code."""
    orig = _FakeDBAPIError(code, "boom")
    return OperationalError("SELECT 1", {}, orig)


class TestGetBackoffDelay:
    """Test suite for get_backoff_delay function."""

    def test_first_attempt_returns_first_delay(self) -> None:
        """Test first retry attempt (index 0) returns first delay."""
        delays = [2.0, 4.0, 8.0]
        assert get_backoff_delay(0, delays) == 2.0

    def test_second_attempt_returns_second_delay(self) -> None:
        """Test second retry attempt (index 1) returns second delay."""
        delays = [2.0, 4.0, 8.0]
        assert get_backoff_delay(1, delays) == 4.0

    def test_third_attempt_returns_third_delay(self) -> None:
        """Test third retry attempt (index 2) returns third delay."""
        delays = [2.0, 4.0, 8.0]
        assert get_backoff_delay(2, delays) == 8.0

    def test_clamps_to_last_delay_when_attempt_exceeds_list(self) -> None:
        """Test clamps to last delay when attempt exceeds list length."""
        delays = [2.0, 4.0, 8.0]
        assert get_backoff_delay(5, delays) == 8.0
        assert get_backoff_delay(10, delays) == 8.0
        assert get_backoff_delay(100, delays) == 8.0

    def test_empty_delays_returns_zero(self) -> None:
        """Test empty delays list returns 0.0."""
        assert get_backoff_delay(0, []) == 0.0
        assert get_backoff_delay(5, []) == 0.0

    def test_single_delay_always_returns_same(self) -> None:
        """Test single delay in list always returns that value."""
        delays = [5.0]
        assert get_backoff_delay(0, delays) == 5.0
        assert get_backoff_delay(1, delays) == 5.0
        assert get_backoff_delay(10, delays) == 5.0

    def test_works_with_integer_delays(self) -> None:
        """Test works with integer delays (coerced to float)."""
        delays = [1.0, 2.0, 3.0]
        assert get_backoff_delay(0, delays) == 1.0
        assert get_backoff_delay(1, delays) == 2.0

    def test_works_with_large_delays(self) -> None:
        """Test works with large delay values."""
        delays = [10.0, 20.0, 40.0, 80.0]
        assert get_backoff_delay(0, delays) == 10.0
        assert get_backoff_delay(3, delays) == 80.0
        assert get_backoff_delay(4, delays) == 80.0


class TestIsTransientDbError:
    """Test suite for is_transient_db_error function."""

    @pytest.mark.parametrize("code", [2006, 2013, 2003, 1205, 1213])
    def test_transient_mysql_codes(self, code: int) -> None:
        """Recoverable connection/lock error codes are transient."""
        assert is_transient_db_error(_operational_error(code)) is True

    def test_connection_invalidated_is_transient(self) -> None:
        """A DBAPIError flagged connection_invalidated is transient."""
        orig = _FakeDBAPIError(9999, "unknown")
        err = OperationalError("SELECT 1", {}, orig, connection_invalidated=True)
        assert is_transient_db_error(err) is True

    def test_interface_error_with_transient_code(self) -> None:
        """InterfaceError with a transient code is transient."""
        orig = _FakeDBAPIError(2013, "lost")
        err = InterfaceError("SELECT 1", {}, orig)
        assert is_transient_db_error(err) is True

    def test_unknown_operational_code_is_not_transient(self) -> None:
        """An OperationalError with a non-transient code is permanent."""
        assert is_transient_db_error(_operational_error(1064)) is False

    def test_integrity_error_is_not_transient(self) -> None:
        """Duplicate-key / integrity errors must never be retried."""
        orig = _FakeDBAPIError(1062, "duplicate")
        err = IntegrityError("INSERT ...", {}, orig)
        assert is_transient_db_error(err) is False

    def test_programming_error_is_not_transient(self) -> None:
        """Programming errors (bad SQL) are permanent."""
        orig = _FakeDBAPIError(1146, "no such table")
        err = ProgrammingError("SELECT ...", {}, orig)
        assert is_transient_db_error(err) is False

    def test_plain_exception_is_not_transient(self) -> None:
        """Non-DB exceptions are not transient."""
        assert is_transient_db_error(ValueError("nope")) is False


class TestRunWithRetry:
    """Test suite for run_with_retry function."""

    @patch("common.utils.retry_utils.time.sleep")
    def test_returns_result_without_retry_on_success(
        self, mock_sleep: MagicMock
    ) -> None:
        """A successful call returns immediately and never sleeps."""
        fn = MagicMock(return_value="ok")
        assert run_with_retry(fn, op="t") == "ok"
        assert fn.call_count == 1
        mock_sleep.assert_not_called()

    @patch("common.utils.retry_utils.time.sleep")
    def test_retries_then_succeeds_on_transient_error(
        self, mock_sleep: MagicMock
    ) -> None:
        """A transient failure is retried and the later success returned."""
        fn = MagicMock(side_effect=[_operational_error(2013), "ok"])
        assert run_with_retry(fn, op="t") == "ok"
        assert fn.call_count == 2
        mock_sleep.assert_called_once()

    @patch("common.utils.retry_utils.time.sleep")
    def test_raises_after_exhausting_attempts(self, mock_sleep: MagicMock) -> None:
        """Persistent transient errors are re-raised after max_attempts."""
        fn = MagicMock(side_effect=_operational_error(2013))
        with pytest.raises(OperationalError):
            run_with_retry(fn, op="t", max_attempts=3)
        assert fn.call_count == 3
        # Sleeps between attempts only (not after the final failure).
        assert mock_sleep.call_count == 2

    @patch("common.utils.retry_utils.time.sleep")
    def test_permanent_error_is_not_retried(self, mock_sleep: MagicMock) -> None:
        """A permanent error raises immediately without retrying."""
        orig = _FakeDBAPIError(1062, "duplicate")
        fn = MagicMock(side_effect=IntegrityError("INSERT", {}, orig))
        with pytest.raises(IntegrityError):
            run_with_retry(fn, op="t")
        assert fn.call_count == 1
        mock_sleep.assert_not_called()


def _mock_engine_conn() -> tuple[MagicMock, MagicMock]:
    """Return (engine, conn) where engine.connect() yields conn as a CM."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None
    return engine, conn


class TestExecuteWithRetry:
    """Test suite for execute_with_retry."""

    def test_executes_commits_and_returns_result(self) -> None:
        """Executes the statement, commits, and returns the cursor result."""
        engine, conn = _mock_engine_conn()
        result = MagicMock()
        conn.execute.return_value = result
        stmt = MagicMock()

        out = execute_with_retry(engine, stmt, {"a": 1}, op="t")

        assert out is result
        conn.execute.assert_called_once_with(stmt, {"a": 1})
        conn.commit.assert_called_once()

    def test_does_not_commit_when_commit_false(self) -> None:
        """commit=False skips the commit (used for reads)."""
        engine, conn = _mock_engine_conn()
        execute_with_retry(engine, MagicMock(), op="t", commit=False)
        conn.commit.assert_not_called()

    @patch("common.utils.retry_utils.time.sleep")
    def test_retries_transient_error_on_fresh_connection(
        self, mock_sleep: MagicMock
    ) -> None:
        """A transient error is retried on a brand-new connection."""
        engine, conn = _mock_engine_conn()
        good = MagicMock()
        conn.execute.side_effect = [_operational_error(2013), good]

        out = execute_with_retry(engine, MagicMock(), op="t")

        assert out is good
        assert engine.connect.call_count == 2
        assert conn.execute.call_count == 2


class TestFetchAllWithRetry:
    """Test suite for fetch_all_with_retry."""

    def test_returns_all_rows(self) -> None:
        """Fetches and returns all rows from the statement."""
        engine, conn = _mock_engine_conn()
        rows = [MagicMock(), MagicMock()]
        conn.execute.return_value.fetchall.return_value = rows

        out = fetch_all_with_retry(engine, MagicMock(), op="t")

        assert out == rows
        conn.commit.assert_not_called()

    @patch("common.utils.retry_utils.time.sleep")
    def test_retries_transient_error(self, mock_sleep: MagicMock) -> None:
        """A transient error is retried on a fresh connection."""
        engine, conn = _mock_engine_conn()
        rows = [MagicMock()]
        good = MagicMock()
        good.fetchall.return_value = rows
        conn.execute.side_effect = [_operational_error(2013), good]

        out = fetch_all_with_retry(engine, MagicMock(), op="t")

        assert out == rows
        assert engine.connect.call_count == 2
