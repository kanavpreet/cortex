"""Retry utilities for handling backoff logic."""

import random
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy import CursorResult, Engine, Executable, Row
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# pymysql error codes that indicate a recoverable connection/lock failure:
#   2006 - MySQL server has gone away
#   2013 - Lost connection to MySQL server during query
#   2003 - Can't connect to MySQL server
#   1205 - Lock wait timeout exceeded
#   1213 - Deadlock found when trying to get lock
_TRANSIENT_MYSQL_CODES = frozenset({2006, 2013, 2003, 1205, 1213})

# Default backoff schedule (seconds) for transient DB retries. Kept short
# because in scribe a retry holds a max_concurrent_writes semaphore slot.
_DEFAULT_DB_RETRY_DELAYS = [0.2, 0.5, 1.0]


def get_backoff_delay(attempt: int, delays: list[float]) -> float:
    """
    Get the backoff delay for a given retry attempt.

    Uses the delay at the attempt index, or clamps to the last delay
    if the attempt exceeds the number of configured delays.

    Args:
        attempt: Zero-based retry attempt number (0 for first retry, 1 for second, etc.)
        delays: List of backoff delays in seconds.

    Returns:
        The backoff delay in seconds for the given attempt.

    Example:
        delays = [2.0, 4.0, 8.0]
        get_backoff_delay(0, delays)  # Returns 2.0
        get_backoff_delay(1, delays)  # Returns 4.0
        get_backoff_delay(2, delays)  # Returns 8.0
        get_backoff_delay(5, delays)  # Returns 8.0 (clamped to last)
    """
    if not delays:
        return 0.0

    index = min(attempt, len(delays) - 1)
    return delays[index]


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def compute_backoff_with_jitter(
    receive_count: int, backoff_tiers: list[float], jitter_fraction: float
) -> float:
    """Compute a backoff delay with ±20% jitter based on the SQS receive count.

    Maps receive_count to one of three tiers: [10s, 30s, 60s]. For receive
    counts beyond the tier list, the maximum tier is used.

    Args:
        receive_count: The SQS ApproximateReceiveCount for the message (1-based).
        backoff_tiers: List of backoff delays in seconds.
        jitter_fraction: Fraction of jitter to apply (e.g., 0.2 for ±20%).

    Returns:
        Backoff duration in seconds with ±20% random jitter applied.
    """
    tier_index = min(receive_count - 1, len(backoff_tiers) - 1)
    base = backoff_tiers[tier_index]
    jitter = base * jitter_fraction * (2 * random.random() - 1)
    return max(0.0, base + jitter)


# ---------------------------------------------------------------------------
# Transient database error retry
# ---------------------------------------------------------------------------


def is_transient_db_error(err: BaseException) -> bool:
    """Return True if a DB error is a transient connection/lock failure.

    Transient failures are safe to retry on a fresh connection. Everything
    else (integrity errors, programming errors, data errors) is permanent and
    must not be retried.

    Args:
        err: The exception raised while executing a query.

    Returns:
        True if the error looks like a recoverable connection/lock failure.
    """
    # Most reliable signal: SQLAlchemy already invalidated the pooled
    # connection, so a fresh one is needed (covers 2006/2013 cleanly).
    if isinstance(err, DBAPIError) and getattr(err, "connection_invalidated", False):
        return True

    if isinstance(err, OperationalError | InterfaceError):
        orig = getattr(err, "orig", None)
        args = getattr(orig, "args", None)
        if args and isinstance(args[0], int) and args[0] in _TRANSIENT_MYSQL_CODES:
            return True

    return False


def run_with_retry[T](
    fn: Callable[[], T],
    *,
    op: str = "db_op",
    max_attempts: int = 3,
    backoff_delays: list[float] | None = None,
    jitter: float = 0.2,
) -> T:
    """Run ``fn`` with retries on transient database errors.

    ``fn`` is invoked up to ``max_attempts`` times. On a transient DB error a
    fresh attempt runs after an exponential-ish backoff (from ``backoff_delays``)
    with +/- ``jitter`` randomisation. Permanent errors, and the final transient
    error after attempts are exhausted, are re-raised so the caller's existing
    error handling still applies.

    IMPORTANT: ``fn`` MUST acquire its own fresh connection on each call (i.e.
    open ``with engine.connect() as conn:`` inside ``fn``). Reusing an
    invalidated connection across attempts would defeat the retry.

    Args:
        fn: Zero-arg callable performing the DB work and returning its result.
        op: Short label for log lines (e.g. "upsert:incidentio_incidents").
        max_attempts: Total attempts including the first (must be >= 1).
        backoff_delays: Per-attempt base delays in seconds; clamps to the last
            entry for later attempts. Defaults to [0.2, 0.5, 1.0].
        jitter: Fractional jitter applied to each delay (e.g. 0.2 for +/-20%).

    Returns:
        Whatever ``fn`` returns on the first successful attempt.
    """
    delays = backoff_delays if backoff_delays is not None else _DEFAULT_DB_RETRY_DELAYS

    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as err:
            if attempt + 1 >= max_attempts or not is_transient_db_error(err):
                if attempt > 0:
                    logger.error(
                        "db retry exhausted",
                        op=op,
                        attempts=attempt + 1,
                        error=str(err),
                    )
                raise
            base = get_backoff_delay(attempt, delays)
            delay = max(0.0, base * (1 + random.uniform(-jitter, jitter)))
            logger.warning(
                "transient db error, retrying",
                op=op,
                attempt=attempt + 1,
                next_attempt_in_s=round(delay, 3),
                error=str(err),
            )
            time.sleep(delay)

    # Unreachable: the loop either returns or raises. Present for type checkers.
    raise RuntimeError("run_with_retry exhausted without returning or raising")


def execute_with_retry(
    engine: Engine,
    statement: Executable,
    parameters: Mapping[str, Any] | None = None,
    *,
    op: str = "db_op",
    commit: bool = True,
    max_attempts: int = 3,
    backoff_delays: list[float] | None = None,
    jitter: float = 0.2,
) -> CursorResult[Any]:
    """Execute a DML statement on a fresh connection with transient-error retry.

    Convenience wrapper over :func:`run_with_retry` for the common
    "open connection, execute, commit" pattern. Each attempt opens a new
    connection so an invalidated one is never reused.

    Use this for INSERT/UPDATE/DELETE: the returned ``CursorResult`` exposes
    ``rowcount`` / ``lastrowid``, which stay valid after the connection is
    returned to the pool. For SELECTs that must read rows, use
    :func:`fetch_all_with_retry` instead (rows must be fetched while the
    connection is open).

    Args:
        engine: SQLAlchemy engine to acquire a connection from.
        statement: The statement to execute (Core construct or ``text()``).
        parameters: Optional bound parameters.
        op: Short label for log lines (e.g. "upsert:incidentio_incidents").
        commit: Whether to commit after executing (True for writes).
        max_attempts: Total attempts including the first.
        backoff_delays: Per-attempt base delays in seconds.
        jitter: Fractional jitter applied to each delay.

    Returns:
        The ``CursorResult`` from the successful execution.
    """

    def _do() -> CursorResult[Any]:
        with engine.connect() as conn:
            result = conn.execute(statement, parameters)
            if commit:
                conn.commit()
            return result

    return run_with_retry(
        _do,
        op=op,
        max_attempts=max_attempts,
        backoff_delays=backoff_delays,
        jitter=jitter,
    )


def fetch_all_with_retry(
    engine: Engine,
    statement: Executable,
    parameters: Mapping[str, Any] | None = None,
    *,
    op: str = "db_op",
    max_attempts: int = 3,
    backoff_delays: list[float] | None = None,
    jitter: float = 0.2,
) -> Sequence[Row[Any]]:
    """Run a SELECT on a fresh connection with retry and return all rows.

    Rows are fetched while the connection is open (so they remain valid after
    it is returned to the pool), then the whole operation is retried on a
    fresh connection if a transient DB error occurs.

    Args:
        engine: SQLAlchemy engine to acquire a connection from.
        statement: The SELECT statement to execute.
        parameters: Optional bound parameters.
        op: Short label for log lines.
        max_attempts: Total attempts including the first.
        backoff_delays: Per-attempt base delays in seconds.
        jitter: Fractional jitter applied to each delay.

    Returns:
        All result rows.
    """

    def _do() -> Sequence[Row[Any]]:
        with engine.connect() as conn:
            return conn.execute(statement, parameters).fetchall()

    return run_with_retry(
        _do,
        op=op,
        max_attempts=max_attempts,
        backoff_delays=backoff_delays,
        jitter=jitter,
    )
