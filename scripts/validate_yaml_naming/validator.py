"""YAML naming convention validation logic."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Files/directories to exclude from validation (relative to repo root)
# Patterns ending with / are treated as directory prefixes
EXCLUDE_PATTERNS: list[str] = [
    "matik/local-configs/grafana/provisioning/",  # Grafana configs use camelCase
    "jira-backstage-mapping.yml",  # Uses non-snake_case keys by design (service names)
]


@dataclass
class ValidationError:
    """Validation error for a YAML key."""

    path: str
    key: str
    message: str


@dataclass
class FileValidationResult:
    """Result of validating a YAML file."""

    filename: str
    success: bool
    errors: list[ValidationError]


def should_exclude_file(filepath: Path, repo_root: Path) -> bool:
    """
    Check if a file should be excluded from validation.

    Args:
        filepath: Path to the file
        repo_root: Root of the repository

    Returns:
        True if the file should be excluded, False otherwise

    Note:
        Patterns ending with / are treated as directory prefixes.
        Other patterns are matched exactly or as suffixes.
    """
    try:
        rel_path = str(filepath.relative_to(repo_root))
    except ValueError:
        rel_path = str(filepath)

    for pattern in EXCLUDE_PATTERNS:
        # Directory prefix pattern (ends with /)
        if pattern.endswith("/"):
            if rel_path.startswith(pattern):
                return True
        # Exact or suffix match
        elif rel_path == pattern or rel_path.endswith(pattern):
            return True
    return False


def is_snake_case(s: str) -> bool:
    """
    Check if a string is in snake_case format.

    Rules:
    - Must start with a lowercase letter
    - Can contain lowercase letters, numbers, and underscores
    - Cannot have consecutive underscores
    - Cannot start or end with underscore

    Args:
        s: String to check

    Returns:
        True if string is valid snake_case, False otherwise
    """
    snake_case_regex = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
    return bool(snake_case_regex.match(s))


def is_template_value(value: Any) -> bool:
    """
    Check if a value contains template syntax (which we should skip).

    Args:
        value: Value to check

    Returns:
        True if value contains template syntax, False otherwise
    """
    if isinstance(value, str):
        # Skip Kubernetes template syntax: {{ .Env.VAR }}
        if "{{" in value and "}}" in value:
            return True
        # Skip envsubst syntax: ${VAR}
        if "${" in value and "}" in value:
            return True
    return False


def normalize_templates(content: str) -> str:
    """
    Normalize template syntax to valid YAML for parsing.

    Replaces templates with placeholder values.

    Args:
        content: Raw YAML content

    Returns:
        Normalized YAML content
    """
    # Replace kube templates (with optional pipe operators like | quote)
    kube_pattern = r"\{\{\s*\.(Env|App\.Secrets)(?:\.[\w.]+)?(?:\s*\|[^}]*)?\s*\}\}"
    normalized = re.sub(kube_pattern, "TEMPLATE_VALUE", content)

    # Replace envsubst templates
    local_pattern = r"\$\{(\w+)\}"
    normalized = re.sub(local_pattern, "TEMPLATE_VALUE", normalized)

    # Blank standalone Go-template control-flow lines (`{{ if ... }}`, `{{ else }}`,
    # `{{ end }}`) -- these carry no YAML value of their own, so a placeholder
    # substitution doesn't apply; drop the line's content instead.
    control_flow_pattern = r"^[ \t]*\{\{-?\s*(?:if\b.*?|else\b.*?|end)\s*-?\}\}[ \t]*$"
    normalized = re.sub(control_flow_pattern, "", normalized, flags=re.MULTILINE)

    return normalized


def validate_yaml_keys(data: Any, path: str) -> list[ValidationError]:  # noqa: C901, PLR0912
    """
    Recursively validate YAML keys follow snake_case.

    Args:
        data: YAML data to validate
        path: Current path in structure (for error messages)

    Returns:
        List of validation errors
    """
    errors: list[ValidationError] = []

    if isinstance(data, dict):
        for key, value in data.items():
            # Build the current path
            current_path = key if not path else f"{path}.{key}"

            # Skip validation if the value is a template
            if is_template_value(value):
                continue

            # Check if key follows snake_case
            if not is_snake_case(key):
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=(
                            f"Key '{key}' does not follow snake_case naming convention"
                        ),
                    )
                )

            # Additional validations
            if " " in key:
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=f"Key '{key}' contains spaces",
                    )
                )

            if "-" in key:
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=(
                            f"Key '{key}' contains hyphens (use underscores instead)"
                        ),
                    )
                )

            # Check for invalid characters
            invalid_chars_regex = re.compile(r"[^a-z0-9_]")
            if invalid_chars_regex.search(key):
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=(
                            f"Key '{key}' contains invalid characters "
                            "(only lowercase letters, numbers, and "
                            "underscores allowed)"
                        ),
                    )
                )

            # Check for consecutive underscores
            if "__" in key:
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=f"Key '{key}' contains consecutive underscores",
                    )
                )

            # Check if key starts or ends with underscore
            if key.startswith("_") or key.endswith("_"):
                errors.append(
                    ValidationError(
                        path=current_path,
                        key=key,
                        message=f"Key '{key}' starts or ends with underscore",
                    )
                )

            # Recurse into nested structures
            errors.extend(validate_yaml_keys(value, current_path))

    elif isinstance(data, list):
        # For arrays, validate each item
        for i, item in enumerate(data):
            item_path = f"{path}[{i}]"
            errors.extend(validate_yaml_keys(item, item_path))

    return errors


def validate_yaml_file(filepath: Path) -> tuple[bool, list[ValidationError]]:
    """
    Load and validate a YAML file.

    Args:
        filepath: Path to YAML file

    Returns:
        Tuple of (success, errors)
    """
    try:
        content = filepath.read_text()
    except Exception as e:
        return False, [
            ValidationError(
                path=str(filepath),
                key="",
                message=f"Failed to read file: {e}",
            )
        ]

    # Normalize template syntax before parsing
    normalized = normalize_templates(content)

    try:
        yaml_data = yaml.safe_load(normalized)
    except yaml.YAMLError as e:
        return False, [
            ValidationError(
                path=str(filepath),
                key="",
                message=f"Failed to parse YAML: {e}",
            )
        ]

    # If file is empty, skip validation
    if yaml_data is None:
        return True, []

    errors = validate_yaml_keys(yaml_data, "")
    return len(errors) == 0, errors


def main() -> None:  # noqa: C901
    """Main entry point for YAML naming validator."""
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

    print(f"\n{COLOR_BOLD}Matik YAML Naming Convention Validator{COLOR_RESET}")
    print("============================================================")

    # Get repo root
    repo_root = Path.cwd()

    # If running from scripts/, go up one level
    if repo_root.name == "scripts":
        repo_root = repo_root.parent

    # Directories to check
    dirs_to_check = [
        repo_root / "_infra" / "kube" / "files",
        repo_root / "matik" / "local-configs",
    ]

    all_files: list[Path] = []

    # Find all YAML files in the directories
    for directory in dirs_to_check:
        if not directory.exists():
            print_warning(f"Directory not found: {directory}")
            continue

        all_files.extend(directory.rglob("*.yaml"))
        all_files.extend(directory.rglob("*.yml"))

    if not all_files:
        print_warning("No YAML files found to validate")
        raise SystemExit(0)

    # Filter out excluded files
    files_to_validate = [f for f in all_files if not should_exclude_file(f, repo_root)]
    excluded_count = len(all_files) - len(files_to_validate)

    print_info(
        f"Found {len(all_files)} YAML file(s), validating {len(files_to_validate)}"
    )
    if excluded_count > 0:
        print_info(f"Excluded {excluded_count} file(s) from validation")
    print()

    # Validate each file
    results: list[FileValidationResult] = []
    all_passed = True

    for file_path in files_to_validate:
        try:
            rel_path = file_path.relative_to(repo_root)
        except ValueError:
            rel_path = file_path

        print(f"Validating: {rel_path}")

        success, errors = validate_yaml_file(file_path)

        if success:
            print_success("  All keys follow snake_case convention")
            results.append(
                FileValidationResult(filename=str(rel_path), success=True, errors=[])
            )
        else:
            print_error("  Validation failed:")
            for error in errors:
                print(f"    {error.path}: {error.message}")
            all_passed = False
            results.append(
                FileValidationResult(
                    filename=str(rel_path), success=False, errors=errors
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
        print_success("All YAML naming validations passed! ✨")
        raise SystemExit(0)
    else:
        print_error(
            "Some YAML files have naming convention violations. "
            "Please fix the errors above."
        )
        print()
        print_info("Naming rules:")
        print("  - Keys must use snake_case (lowercase with underscores)")
        print("  - Keys must start with a lowercase letter")
        print("  - Keys cannot contain spaces, hyphens, or special characters")
        print("  - Keys cannot have consecutive underscores")
        print("  - Keys cannot start or end with underscores")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
