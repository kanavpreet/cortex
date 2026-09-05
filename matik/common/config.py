"""
Configuration loading utilities.

This module provides configuration loading from YAML files with environment variable
substitution, similar to viper in Go.

Example:
    >>> config = load_config("config/matik-historian-config.yml")
    >>> print(config.common.environment)
    'production'
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

from common.models import MatikConfig


def _substitute_env_vars(data: Any) -> Any:
    """
    Recursively substitute environment variables in config data.

    Replaces ${VAR_NAME} with environment variable values.

    Args:
        data: Configuration data (dict, list, str, or primitive)

    Returns:
        Configuration data with environment variables substituted
    """
    if isinstance(data, dict):
        return {key: _substitute_env_vars(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [_substitute_env_vars(item) for item in data]
    elif isinstance(data, str):
        # Replace ${VAR_NAME} with environment variable value
        pattern = re.compile(r"\$\{([^}]+)\}")

        def replace_var(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        result = pattern.sub(replace_var, data)
        return None if result == "" else result
    else:
        return data


def load_config(config_path: str | Path) -> MatikConfig:
    """
    Load configuration from YAML file with environment variable substitution.

    Args:
        config_path: Path to YAML configuration file (relative to /app/config or absolute)

    Returns:
        Validated MatikConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML parsing fails
        pydantic.ValidationError: If config validation fails

    Example:
        >>> config = load_config("matik-historian-config.yml")
        >>> print(config.common.environment)
    """
    config_path = Path(config_path)

    # If not absolute, look in /app/config
    if not config_path.is_absolute():
        config_path = Path("/app/config") / config_path

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Read YAML file
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    # Substitute environment variables
    config_data = _substitute_env_vars(raw_config)

    # Validate and return
    return MatikConfig(**config_data)


def merge_config(base_config: MatikConfig, additional_path: str | Path) -> MatikConfig:
    """
    Merge additional config file into base config.

    Args:
        base_config: Base configuration object
        additional_path: Path to additional YAML config file (relative to /app/config or absolute)

    Returns:
        New MatikConfig with merged configuration

    Example:
        >>> base = load_config("base.yaml")
        >>> merged = merge_config(base, "other.yaml")
    """
    additional_path = Path(additional_path)

    # If not absolute, look in /app/config
    if not additional_path.is_absolute():
        additional_path = Path("/app/config") / additional_path

    if not additional_path.exists():
        raise FileNotFoundError(f"Config file not found: {additional_path}")

    # Read additional config
    with open(additional_path) as f:
        additional_data = yaml.safe_load(f)

    # Substitute environment variables
    additional_data = _substitute_env_vars(additional_data)

    # Merge configs (additional overrides base)
    base_dict = base_config.model_dump()
    merged_dict = {**base_dict, **additional_data}

    return MatikConfig(**merged_dict)
