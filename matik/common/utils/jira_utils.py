"""JIRA utility functions."""

import re
from collections.abc import Sequence
from typing import Any

# Custom field keys for TCMR Related to TCMR Planned END Date
# NOTE: This list may grow as edits are performed on the Jira custom fields.
# The fields are checked in order, and the first non-empty value is used.
CUSTOM_FIELDS_TCMR_RELATED_PLANNED_START_DATE = [
    "customfield_26927",
]

# Custom field keys for TCMR Related to TCMR Planned END Date
# NOTE: This list may grow as edits are performed on the Jira custom fields.
# The fields are checked in order, and the first non-empty value is used.
CUSTOM_FIELDS_TCMR_RELATED_PLANNED_END_DATE = [
    "customfield_26928",
    "customfield_26926",
]

# Custom field keys for TCMR related Git PR link.
# NOTE: This list may grow as edits are performed on the Jira custom fields.
# The fields are checked in order, and the first non-empty value is used.
CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK = [
    "customfield_16509",
    "customfield_38827",
    "customfield_40901",
]

# Custom field keys for TCMR related services.
# NOTE: This list may grow as edits are performed on the Jira custom fields.
# The fields are checked in order, and the first non-empty value is used.
CUSTOM_FIELDS_TCMR_RELATED_SERVICES = [
    "customfield_29146",
    "customfield_31221",
]

# Delimiter used to separate comments when aggregating for LLM processing
COMMENT_DELIMITER = " ;; "

# Regex pattern for TCMR links. The capture group isolates the issue key so the
# same anchored URL match backs both extract_tcmr_link and extract_tcmr_key.
_TCMR_LINK_PATTERN = re.compile(r"https://jira\.airbnb\.biz/browse/(TCMR-\d+)")


def extract_tcmr_link(text: str) -> str:
    """
    Extract a TCMR link from the given text.

    Args:
        text: Text to search for TCMR link

    Returns:
        First TCMR link found (https://jira.airbnb.biz/browse/TCMR-<number>),
        or empty string if not found
    """
    match = _TCMR_LINK_PATTERN.search(text)
    return match.group(0) if match else ""


def extract_tcmr_key(text: str) -> str:
    """
    Extract the TCMR issue key (e.g. "TCMR-12345") from a TCMR browse URL in text.

    Anchors on the canonical browse URL (not a bare ``TCMR-<number>`` mention) to
    avoid false positives from prose.

    Args:
        text: Text to search for a TCMR link

    Returns:
        The first TCMR issue key found, or empty string if not found
    """
    match = _TCMR_LINK_PATTERN.search(text)
    return match.group(1) if match else ""


def aggregate_comments_for_llm(comments: Sequence[str | None] | None) -> str:
    """
    Aggregate comment bodies into a single string for LLM processing.

    Args:
        comments: List of comment body strings

    Returns:
        Single string with all non-empty comments joined by COMMENT_DELIMITER,
        or empty string if no valid comments
    """
    if not comments:
        return ""

    bodies = [body.strip() for body in comments if body and body.strip()]

    if not bodies:
        return ""

    return COMMENT_DELIMITER.join(bodies)


def get_string_list_field(
    fields: dict[str, Any] | None, keys: list[str]
) -> list[str] | None:
    """
    Get a list of strings from a JIRA custom field, checking multiple keys in order.

    Handles common JIRA field formats:
    - String: "service-abc" → ["service-abc"]
    - List of strings: ["service-a", "service-b"] → as-is
    - List of objects with 'value' key: [{"value": "svc"}] → ["svc"]

    Args:
        fields: Dictionary to search (e.g., JIRA unknowns/custom fields)
        keys: List of keys to check in order

    Returns:
        List of non-empty strings found, or None if no valid data
    """
    if fields is None:
        return None

    for key in keys:
        value = fields.get(key)
        if value is None:
            continue

        if isinstance(value, str) and value.strip():
            return [value.strip()]

        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    result.append(item.strip())
                elif isinstance(item, dict):
                    # JIRA multi-select fields return objects with 'value' key
                    v = item.get("value") or item.get("name") or item.get("id")
                    if isinstance(v, str) and v.strip():
                        result.append(v.strip())
            if result:
                return result

    return None


def get_first_non_empty_string_field(
    fields: dict[str, Any] | None, keys: list[str]
) -> str:
    """
    Get the first non-empty string value from a dict, checking multiple keys in order.

    Args:
        fields: Dictionary to search (e.g., JIRA unknowns/custom fields)
        keys: List of keys to check in order

    Returns:
        First non-empty string value found, or empty string if none found
    """
    if fields is None:
        return ""

    for key in keys:
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value

    return ""
