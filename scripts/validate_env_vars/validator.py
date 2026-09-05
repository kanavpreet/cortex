"""Environment variable validation logic."""

import re
from pathlib import Path


def extract_env_variables(content: str) -> list[str]:
    """
    Extract variable names from envsubst syntax ${VAR}.

    Args:
        content: File content to scan

    Returns:
        Sorted list of unique variable names
    """
    pattern = r"\$\{(\w+)\}"
    matches = re.findall(pattern, content)

    # Use set to remove duplicates, then sort
    var_set = set(matches)
    return sorted(var_set)


def parse_env_example(filepath: Path) -> dict[str, bool]:
    """
    Parse .env.example file and extract variable names.

    Args:
        filepath: Path to .env.example file

    Returns:
        Dictionary mapping variable names to True

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    content = filepath.read_text()
    var_set: dict[str, bool] = {}

    # Regex to match env var declarations: VAR_NAME=value
    env_var_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)

    for line in content.splitlines():
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue

        # Match variable declaration
        match = env_var_pattern.match(line)
        if match:
            var_name = match.group(1)
            var_set[var_name] = True

    return var_set


def scan_local_configs(local_configs_dir: Path) -> dict[str, list[str]]:
    """
    Scan local-configs directory for all YAML files and extract env vars.

    Args:
        local_configs_dir: Path to local-configs directory

    Returns:
        Dictionary mapping filenames to lists of env vars used

    Raises:
        FileNotFoundError: If directory doesn't exist
    """
    if not local_configs_dir.exists():
        raise FileNotFoundError(f"Directory not found: {local_configs_dir}")

    file_vars: dict[str, list[str]] = {}

    # Find all YAML files
    for path in local_configs_dir.rglob("*.yaml"):
        if path.is_file():
            content = path.read_text()
            vars_list = extract_env_variables(content)
            if vars_list:
                file_vars[path.name] = vars_list

    for path in local_configs_dir.rglob("*.yml"):
        if path.is_file():
            content = path.read_text()
            vars_list = extract_env_variables(content)
            if vars_list:
                file_vars[path.name] = vars_list

    return file_vars


def main() -> None:
    """Main entry point for env vars validator."""
    from common import (
        COLOR_BOLD,
        COLOR_RED,
        COLOR_RESET,
        print_error,
        print_info,
        print_success,
        print_warning,
    )

    print(f"\n{COLOR_BOLD}Matik Environment Variables Validator{COLOR_RESET}")
    print("============================================================")

    # Define paths
    repo_root = Path.cwd()

    # If running from scripts/, go up one level
    if repo_root.name == "scripts":
        repo_root = repo_root.parent

    local_configs_dir = repo_root / "matik" / "local-configs"
    env_example_path = repo_root / "matik" / ".env.example"

    # Check directories and files exist
    if not local_configs_dir.exists():
        print_error(f"Local configs directory not found: {local_configs_dir}")
        raise SystemExit(1)

    if not env_example_path.exists():
        print_error(f".env.example file not found: {env_example_path}")
        raise SystemExit(1)

    # Parse .env.example
    print_info("Parsing .env.example...")
    env_vars = parse_env_example(env_example_path)

    if not env_vars:
        print_warning("No environment variables found in .env.example")
    else:
        print_success(f"Found {len(env_vars)} environment variables in .env.example")
    print()

    # Scan local-configs for env var usage
    print_info("Scanning local-configs/ for environment variable references...")
    file_vars = scan_local_configs(local_configs_dir)

    if not file_vars:
        print_warning("No environment variable references found in local-configs/")
        print_success("All config validations passed! ✨")
        raise SystemExit(0)

    print_success(f"Found environment variable references in {len(file_vars)} file(s)")
    print()

    # Validate each file
    all_passed = True
    total_vars = 0
    missing_vars_by_file: dict[str, list[str]] = {}

    # Sort filenames for consistent output
    filenames = sorted(file_vars.keys())

    for filename in filenames:
        vars_list = file_vars[filename]
        total_vars += len(vars_list)

        print(f"Validating: {filename}")
        print_info(
            f"  Found {len(vars_list)} variable reference(s): {', '.join(vars_list)}"
        )

        # Check if all vars are in .env.example
        missing_vars = [var_name for var_name in vars_list if var_name not in env_vars]

        if missing_vars:
            print_error(f"  Missing in .env.example: {', '.join(missing_vars)}")
            missing_vars_by_file[filename] = missing_vars
            all_passed = False
        else:
            print_success("  All variables found in .env.example")

        print()

    # Print summary
    print("============================================================")
    print(f"\n{COLOR_BOLD}Summary{COLOR_RESET}\n")

    print(f"Total config files:        {len(file_vars)}")
    print(f"Total variable references: {total_vars}")
    print(f"Variables in .env.example: {len(env_vars)}")
    print()

    if missing_vars_by_file:
        print(f"{COLOR_RED}Missing Variables:{COLOR_RESET}")
        for filename in filenames:
            if filename in missing_vars_by_file:
                missing = missing_vars_by_file[filename]
                print(f"  {filename}: {', '.join(missing)}")
        print()

    if all_passed:
        print_success("All environment variables are documented in .env.example! ✨")
        raise SystemExit(0)
    else:
        print_error("Some environment variables are missing from .env.example")
        print()
        print_info("Action required:")
        print(
            "  Add the missing variables to .env.example with appropriate "
            "placeholder values"
        )
        print("  Example format:")
        print("    MYSQL_USER=your_mysql_username_here")
        print("    API_KEY=your_api_key_here")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
