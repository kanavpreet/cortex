"""YAML config validation logic for comparing kube and local configs."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Local-only files/directories that have no kube/files counterpart and should be
# skipped during the reverse (local → kube) existence check.
LOCAL_ONLY_FILES = {
    "prometheus.yml",
    "otel-collector-config.yml",
}
LOCAL_ONLY_DIRS = {
    "grafana",
}

# Kube-only files that have no local-configs counterpart and should be skipped
# during the forward (kube → local) existence check. These are Pokey file
# mounts consumed only by CI integration-test runs, not by any local-dev
# service config — see integration-tests/pytest/conftest.py.
KUBE_ONLY_FILES = {
    "integration-tests-secrets.yml",
}


@dataclass
class ValidationResult:
    """Result of validating a config file pair."""

    filename: str
    success: bool
    errors: list[str]


def extract_kube_variables(value: str) -> list[str]:
    """
    Extract variable names from kube config template syntax.

    Patterns: {{ .Env.VARNAME }}, {{ .Env.Params.VARNAME }}, {{ .App.Secrets.VARNAME }}

    Args:
        value: String potentially containing kube templates

    Returns:
        List of extracted variable names
    """
    # Match the entire template expression (with optional pipe operators like | quote)
    pattern = r"\{\{\s*\.(Env|App\.Secrets)(\.[\w.]+)?(?:\s*\|[^}]*)?\s*\}\}"
    matches = re.findall(pattern, value)

    vars_list = []
    for match in matches:
        # match[1] contains the path after .Env or .App.Secrets
        # e.g., ".VAR" or ".Params.VAR"
        if len(match) > 1 and match[1]:
            # Remove leading dot and extract the last component as the var name
            path = match[1].lstrip(".")
            parts = path.split(".")
            if parts:
                vars_list.append(parts[-1])
    return vars_list


def extract_local_variables(value: str) -> list[str]:
    """
    Extract variable names from local config envsubst syntax.

    Pattern: ${VARNAME}

    Args:
        value: String potentially containing envsubst templates

    Returns:
        List of extracted variable names
    """
    pattern = r"\$\{(\w+)\}"
    matches = re.findall(pattern, value)
    return matches


def normalize_templates(content: str) -> str:
    """
    Normalize template syntax to valid YAML for parsing.

    Replaces {{ .Env.VAR }} with TEMPLATE_VALUE and ${VAR} with TEMPLATE_VAR.
    Doesn't add quotes - lets existing YAML quotes handle it to avoid double-quoting.

    Args:
        content: Raw YAML content with templates

    Returns:
        Normalized YAML content
    """
    # Replace kube templates: {{ .Env.VAR }}, {{ .Env.Params.VAR | quote }}, etc.
    kube_pattern = r"\{\{\s*\.(Env|App\.Secrets)(?:\.[\w.]+)?(?:\s*\|[^}]*)?\s*\}\}"
    normalized = re.sub(kube_pattern, "TEMPLATE_VALUE", content)

    # Replace envsubst templates: ${VAR}
    local_pattern = r"\$\{(\w+)\}"
    normalized = re.sub(local_pattern, r"TEMPLATE_\1", normalized)

    # Blank standalone Go-template control-flow lines (`{{ if ... }}`, `{{ else }}`,
    # `{{ end }}`) -- these carry no YAML value of their own, so a placeholder
    # substitution doesn't apply; drop the line's content instead.
    control_flow_pattern = r"^[ \t]*\{\{-?\s*(?:if\b.*?|else\b.*?|end)\s*-?\}\}[ \t]*$"
    normalized = re.sub(control_flow_pattern, "", normalized, flags=re.MULTILINE)

    return normalized


def load_yaml_safe(filepath: Path) -> dict[str, Any]:
    """
    Load YAML file safely, handling template syntax.

    Args:
        filepath: Path to YAML file

    Returns:
        Parsed YAML data as dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
    """
    content = filepath.read_text()

    # Normalize template syntax before parsing
    normalized = normalize_templates(content)

    result: dict[str, Any] = yaml.safe_load(normalized)
    if result is None:
        return {}

    return result


def check_secrets_have_defaults(filepath: Path) -> list[str]:
    """
    Scan raw file content for .App.Secrets references and ensure they have | default.

    Args:
        filepath: Path to file to check

    Returns:
        List of error messages for secrets without defaults
    """
    content = filepath.read_text()
    errors = []

    # Find all .App.Secrets references
    # Match {{ .App.Secrets.path.to.secret }} with optional | default
    secret_pattern = r"\{\{\s*\.App\.Secrets(\.[\w.]+)\s*(\|[^}]*)?\s*\}\}"
    matches = re.findall(secret_pattern, content)

    for match in matches:
        secret_path = match[0]  # e.g., ".incidentio.api_key"
        # e.g., "| default \"\"" or empty
        pipe_clause = match[1] if len(match) > 1 else ""

        # Check if there's a default clause
        if not pipe_clause or "default" not in pipe_clause:
            errors.append(
                f"  .App.Secrets{secret_path}: Missing '| default' clause - "
                "all secrets must have a default value"
            )

    return errors


def compare_yaml_structure(kube_data: Any, local_data: Any, path: str) -> list[str]:  # noqa: C901, PLR0912
    """
    Compare YAML structure recursively.

    Args:
        kube_data: Data from kube config
        local_data: Data from local config
        path: Current path in structure (for error messages)

    Returns:
        List of error messages
    """
    errors: list[str] = []

    # Check if either side is a template variable before comparing types
    if isinstance(local_data, str) and "TEMPLATE_" in local_data:
        return errors
    if isinstance(kube_data, str) and "TEMPLATE_" in kube_data:
        return errors

    # Check if types match
    if type(kube_data) is not type(local_data):
        errors.append(
            f"  {path}: Type mismatch - kube is {type(kube_data).__name__}, "
            f"local is {type(local_data).__name__}"
        )
        return errors

    # Handle dicts
    if isinstance(kube_data, dict):
        local_dict = local_data  # Already confirmed same type above

        # Get keys
        kube_keys = set(kube_data.keys())
        local_keys = set(local_dict.keys())

        # Check for missing keys in local
        missing_in_local = sorted(kube_keys - local_keys)
        if missing_in_local:
            errors.append(
                f"  {path}: Keys missing in local config: {', '.join(missing_in_local)}"
            )

        # Check for extra keys in local
        extra_in_local = sorted(local_keys - kube_keys)
        if extra_in_local:
            errors.append(
                f"  {path}: Extra keys in local config: {', '.join(extra_in_local)}"
            )

        # Recurse into common keys
        for key in kube_keys & local_keys:
            new_path = key if not path else f"{path}.{key}"
            errors.extend(
                compare_yaml_structure(kube_data[key], local_dict[key], new_path)
            )

    # Handle lists
    elif isinstance(kube_data, list):
        local_list = local_data
        if len(kube_data) != len(local_list):
            errors.append(
                f"  {path}: List length mismatch - kube has {len(kube_data)} items, "
                f"local has {len(local_list)} items"
            )

    # Handle strings - check if they contain template placeholders
    elif isinstance(kube_data, str):
        local_str = local_data

        # If local is string and contains TEMPLATE_, skip type checking
        if "TEMPLATE_" in local_str:
            return errors

        # Both are strings - check variable patterns
        kube_vars = extract_kube_variables(kube_data)
        local_vars = extract_local_variables(local_str)

        # Only validate if both have variables
        if kube_vars or local_vars:
            kube_var_set = set(kube_vars)
            local_var_set = set(local_vars)

            # Check if sets match
            if kube_var_set != local_var_set:
                kube_vars_str = (
                    ", ".join(sorted(kube_var_set)) if kube_var_set else "none"
                )
                local_vars_str = (
                    ", ".join(sorted(local_var_set)) if local_var_set else "none"
                )

                errors.append(
                    f"  {path}: Variable mismatch - kube uses [{kube_vars_str}], "
                    f"local uses [{local_vars_str}]"
                )

    return errors


def validate_file_pair(kube_file: Path, local_file: Path) -> tuple[bool, list[str]]:
    """
    Validate a pair of kube and local config files.

    Args:
        kube_file: Path to kube config file
        local_file: Path to local config file

    Returns:
        Tuple of (success, errors)
    """
    try:
        kube_data = load_yaml_safe(kube_file)
    except Exception as e:
        return False, [f"  Failed to load kube file: {e}"]

    try:
        local_data = load_yaml_safe(local_file)
    except Exception as e:
        return False, [f"  Failed to load local file: {e}"]

    if not kube_data and not local_data:
        return False, ["  Both files are empty or failed to parse"]

    errors = compare_yaml_structure(kube_data, local_data, "")

    # Check that all .App.Secrets references have default values
    secrets_errors = check_secrets_have_defaults(kube_file)
    errors.extend(secrets_errors)

    if errors:
        return False, errors

    return True, []


def main() -> None:
    """Main entry point for config validator."""
    from common import (
        COLOR_BOLD,
        COLOR_GREEN,
        COLOR_RED,
        COLOR_RESET,
        print_error,
        print_info,
        print_success,
        print_warning,
    )

    print(f"\n{COLOR_BOLD}Matik Config Validator{COLOR_RESET}")
    print("============================================================")

    # Define paths
    repo_root = Path.cwd()

    # If running from scripts/, go up one level
    if repo_root.name == "scripts":
        repo_root = repo_root.parent

    kube_files_dir = repo_root / "_infra" / "kube" / "files"
    local_configs_dir = repo_root / "matik" / "local-configs"

    # Check directories exist
    if not kube_files_dir.exists():
        print_error(f"Kube files directory not found: {kube_files_dir}")
        raise SystemExit(1)

    if not local_configs_dir.exists():
        print_error(f"Local configs directory not found: {local_configs_dir}")
        raise SystemExit(1)

    # Find all YAML files in kube directory
    kube_files = list(kube_files_dir.rglob("*.yaml")) + list(
        kube_files_dir.rglob("*.yml")
    )

    if not kube_files:
        print_warning(f"No YAML files found in {kube_files_dir}")
        raise SystemExit(0)

    print_info(f"Found {len(kube_files)} file(s) in _infra/kube/files")
    print()

    # Validate each file
    results: list[ValidationResult] = []
    all_passed = True

    for kube_file in kube_files:
        filename = kube_file.name
        if filename in KUBE_ONLY_FILES:
            continue
        local_file = local_configs_dir / filename

        print(f"Validating: {filename}")

        # Check if local file exists
        if not local_file.exists():
            print_error(f"  Missing in local-configs/: {filename}")
            all_passed = False
            results.append(
                ValidationResult(
                    filename=filename,
                    success=False,
                    errors=["File missing in local-configs/"],
                )
            )
            print()
            continue

        # Validate file pair
        success, errors = validate_file_pair(kube_file, local_file)

        if success:
            print_success("  Structure matches")
            results.append(ValidationResult(filename=filename, success=True, errors=[]))
        else:
            print_error("  Validation failed:")
            for error in errors:
                print(error)
            all_passed = False
            results.append(
                ValidationResult(filename=filename, success=False, errors=errors)
            )

        print()

    # Check reverse: every local config must also exist in kube/files
    local_files = list(local_configs_dir.rglob("*.yaml")) + list(
        local_configs_dir.rglob("*.yml")
    )

    kube_filenames = {f.name for f in kube_files}

    print_info(f"Found {len(local_files)} file(s) in matik/local-configs")
    print()

    for local_file in local_files:
        filename = local_file.name
        if filename in kube_filenames:
            # Already validated in the forward pass
            continue
        if filename in LOCAL_ONLY_FILES:
            continue
        if any(part in LOCAL_ONLY_DIRS for part in local_file.parts):
            continue

        print(f"Validating: {filename}")
        print_error(f"  Missing in _infra/kube/files/: {filename}")
        all_passed = False
        results.append(
            ValidationResult(
                filename=filename,
                success=False,
                errors=["File missing in _infra/kube/files/"],
            )
        )
        print()

    # Print summary
    print("============================================================")
    print(f"\n{COLOR_BOLD}Summary{COLOR_RESET}\n")

    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    print(f"Total files:   {len(results)}")
    print(f"{COLOR_GREEN}Passed:        {passed}{COLOR_RESET}")

    if failed > 0:
        print(f"{COLOR_RED}Failed:        {failed}{COLOR_RESET}")

    print()

    if all_passed:
        print_success("All config validations passed! ✨")
        raise SystemExit(0)
    else:
        print_error("Some config validations failed. Please fix the errors above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
