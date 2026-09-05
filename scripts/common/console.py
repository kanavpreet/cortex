"""Console output utilities with ANSI color codes."""

# ANSI color codes
COLOR_RESET = "\033[0m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_BOLD = "\033[1m"


def print_error(msg: str) -> None:
    """
    Print an error message with red color and X symbol.

    Args:
        msg: Error message to print
    """
    print(f"{COLOR_RED}✗ {msg}{COLOR_RESET}")


def print_success(msg: str) -> None:
    """
    Print a success message with green color and checkmark symbol.

    Args:
        msg: Success message to print
    """
    print(f"{COLOR_GREEN}✓ {msg}{COLOR_RESET}")


def print_warning(msg: str) -> None:
    """
    Print a warning message with yellow color and warning symbol.

    Args:
        msg: Warning message to print
    """
    print(f"{COLOR_YELLOW}⚠ {msg}{COLOR_RESET}")


def print_info(msg: str) -> None:
    """
    Print an info message with blue color and info symbol.

    Args:
        msg: Info message to print
    """
    print(f"{COLOR_BLUE}ℹ {msg}{COLOR_RESET}")


def print_progress(
    current: int, total: int | None, prefix: str = "", width: int = 30
) -> None:
    """
    Render an in-place progress bar on the current line (no newline).

    Pass total=None when the final count isn't known yet (e.g. draining a
    queue of unknown depth) — falls back to a running counter instead of a
    filled bar. Call print() with no args once the loop finishes to move
    past the in-place line.

    Args:
        current: Number of items processed so far.
        total: Expected total, or None if unknown.
        prefix: Text shown before the bar/counter.
        width: Bar width in characters.
    """
    label = f"{prefix} " if prefix else ""
    if total is None or total <= 0:
        print(f"\r{label}{current} processed...", end="", flush=True)
        return
    fraction = min(current / total, 1.0)
    filled = int(width * fraction)
    bar = "█" * filled + "░" * (width - filled)
    percent = int(fraction * 100)
    print(f"\r{label}[{bar}] {percent}% ({current}/{total})", end="", flush=True)
