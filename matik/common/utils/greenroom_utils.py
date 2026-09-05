"""Utilities for JIRA-to-Backstage service mapping via Greenroom."""

from typing import Any

import yaml

from common.utils.log_utils import get_logger

logger = get_logger(__name__)


def load_jira_backstage_mapping(path: str) -> dict[str, list[str]]:
    """
    Load JIRA-to-Backstage mapping from a YAML file.

    The YAML structure is:
        "Service Name":
          match_count: N
          matches:
            - backstage_name: "service-id"
              backstage_title: "Service Title"
              score: 90

    Args:
        path: Path to the YAML mapping file

    Returns:
        Dict mapping JIRA service name to list of backstage_name values.
        Entries with match_count 0 are skipped.
    """
    try:
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("backstage mapping file not found", path=path)
        return {}
    except Exception:
        logger.exception("error loading backstage mapping", path=path)
        return {}

    mapping: dict[str, list[str]] = {}

    for jira_service, entry in data.items():
        if not isinstance(entry, dict):
            continue

        match_count = entry.get("match_count", 0)
        if match_count == 0:
            continue

        matches = entry.get("matches", [])
        if not isinstance(matches, list):
            continue

        backstage_names = []
        for match in matches:
            if isinstance(match, dict):
                name = match.get("backstage_name")
                if name:
                    backstage_names.append(str(name))

        if backstage_names:
            mapping[jira_service] = backstage_names

    logger.info(
        "loaded backstage mapping",
        path=path,
        total_entries=len(data),
        mapped_entries=len(mapping),
    )

    return mapping


def resolve_jira_services_from_mapping(
    tcmr_services: list[str],
    mapping: dict[str, list[str]],
) -> list[str]:
    """
    Resolve JIRA TCMR service names to Backstage service names using a mapping.

    For each service name in tcmr_services, looks up the mapping and collects
    all corresponding backstage_name values. Results are deduplicated.

    Args:
        tcmr_services: List of JIRA service names from the TCMR ticket
        mapping: Dict from load_jira_backstage_mapping()

    Returns:
        Deduplicated list of Backstage service names, or empty list if no matches
    """
    if not tcmr_services or not mapping:
        return []

    seen: set[str] = set()
    result: list[str] = []

    for service_name in tcmr_services:
        backstage_names = mapping.get(service_name, [])
        for name in backstage_names:
            if name not in seen:
                seen.add(name)
                result.append(name)

    return result
