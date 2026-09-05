"""Secure temporary file utilities for storing secrets."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def _overwrite_file(file_path: str) -> None:
    """Overwrite file with zeros before deletion to prevent data recovery."""
    try:
        # Read file size
        file_size = os.path.getsize(file_path)

        # Change permissions to writable before overwriting
        os.chmod(file_path, 0o600)

        # Overwrite with zeros
        with open(file_path, "wb") as f:
            f.write(b"\x00" * file_size)

    except (
        OSError
    ) as e:  # pragma: no cover - OS errors during cleanup are rare and hard to simulate
        logger.error(f"Failed to overwrite file for secure deletion: {e}")


@contextmanager
def create_secure_temp_file(prefix: str, content: str) -> Iterator[str]:
    """
    Create a secure temporary file with restricted permissions.

    Creates a temporary file with read-only permissions (0400) containing
    the provided content. The file is securely wiped (overwritten with zeros)
    and deleted when the context exits.

    Escaped newlines (\\n) in content are converted to actual newlines,
    which is useful for PEM keys stored in environment variables.

    Args:
        prefix: Prefix for the temporary file name
        content: Content to write to the file

    Yields:
        Path to the temporary file

    Example:
        with create_secure_temp_file("github-key", private_key_content) as key_path:
            # Use key_path with SDK that requires file path
            client = GitHubClient(private_key_file=key_path)
        # File is securely wiped and deleted after context exits
    """
    file_path: str | None = None

    try:
        # Create temp file with mkstemp for more control
        fd, file_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp")

        # Replace escaped newlines with actual newlines (for PEM keys in env vars)
        unescaped_content = content.replace("\\n", "\n")

        # Write content using the file descriptor
        os.write(fd, unescaped_content.encode())
        os.close(fd)

        # Set restrictive permissions (read-only by owner)
        os.chmod(file_path, 0o400)

        yield file_path

    finally:
        # Secure cleanup
        if file_path and os.path.exists(file_path):
            _overwrite_file(file_path)
            try:
                os.remove(file_path)
            except OSError as e:  # pragma: no cover - OS errors during cleanup are rare and hard to simulate
                logger.error(f"Failed to remove temporary secret file: {e}")
