#!/bin/bash
# Wrapper script to run Python env vars validator

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Change to scripts directory
cd "$SCRIPT_DIR"

# Run the Python validator using uv
uv run python -m validate_env_vars
