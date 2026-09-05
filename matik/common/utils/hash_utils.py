"""Hash utilities."""

import hashlib


def generate_string_hash(s: str) -> str:
    """
    Generate SHA256 hash of the given string.

    Args:
        s: String to hash

    Returns:
        Hexadecimal string representation of the SHA256 hash
    """
    return hashlib.sha256(s.encode()).hexdigest()
