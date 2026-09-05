"""Polling utility for waiting on async conditions."""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def poll_until(
    condition: Callable[[], T | None],
    timeout: float = 300.0,
    interval: float = 5.0,
    description: str = "condition",
) -> T:
    """
    Poll condition() every interval seconds until it returns a truthy value or timeout.

    Returns the truthy value returned by condition().
    Raises TimeoutError if timeout is reached without a truthy return.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(
        f"Timed out after {timeout}s waiting for: {description}"
    )
