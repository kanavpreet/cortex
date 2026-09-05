"""Environment variable utilities."""

import os

from common.models.common_config import CommonConfig


def is_local_environment(common_config: CommonConfig | None = None) -> bool:
    """
    Check if the current environment is local.

    Args:
        common_config: Common configuration object. If None, defaults to local.

    Returns:
        True if running in local environment, False otherwise.
    """
    if common_config is None:
        return True

    environment = common_config.environment
    return environment in ("", "local")


def get_env_variable(key: str, default: str | None = None) -> str:
    """
    Get environment variable value.

    Args:
        key: Environment variable name
        default: Default value if not set. If None and variable is not set,
                 raises ValueError.

    Returns:
        Environment variable value or default

    Raises:
        ValueError: If variable is not set and no default provided
    """
    value = os.getenv(key)
    if value:
        return value

    if default is not None:
        return default

    raise ValueError(f"Environment variable {key} is not set")


def determine_environment(env: str) -> str:
    """
    Normalize environment string to standard format.

    Args:
        env: Environment string (e.g., "prod", "staging", "dev")

    Returns:
        Normalized environment name: "production", "staging", "development",
        or empty string if unrecognized
    """
    env_lower = env.lower()

    if env_lower in ("prod", "production", "main", "master"):
        return "production"
    elif env_lower in ("staging", "stage"):
        return "staging"
    elif env_lower in ("dev", "development"):
        return "development"
    else:
        return ""


def extract_service(name: str) -> str:
    """
    Extract service name from module path.

    Args:
        name: Module name (e.g., "common.utils.log_utils", "historian.biztech_github.main")

    Returns:
        Service name (e.g., "historian", "api", "chronicler") or "unknown"

    Examples:
        >>> extract_service(__name__)
        'historian'
        >>> _extract_service("__main__")
        'unknown'
    """
    parts = name.split(".")
    # Get first component as service name
    if len(parts) >= 1 and parts[0] not in ("__main__", ""):
        return parts[0]
    return "unknown"


def extract_full_service(name: str) -> str:
    """
    Extract full service name from module path, including submodule.

    Args:
        name: Module name (e.g., "historian.incidentio.main", "historian.jira.main")

    Returns:
        Full service name (e.g., "historian-incidentio", "historian-jira") or "unknown"

    Examples:
        >>> extract_full_service("historian.incidentio.main")
        'historian-incidentio'
        >>> extract_full_service("api.main")
        'api'
        >>> extract_full_service("__main__")
        'unknown'
    """
    parts = name.split(".")
    if len(parts) >= 2 and parts[0] not in ("__main__", ""):
        # Skip "main" as second part - it's just the entry point
        if parts[1] == "main":
            return parts[0]
        return f"{parts[0]}-{parts[1]}"
    if len(parts) >= 1 and parts[0] not in ("__main__", ""):
        return parts[0]
    return "unknown"
