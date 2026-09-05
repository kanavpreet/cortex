#!/usr/bin/env bash
#
# seed_dataset.sh — Interactive uploader for Matik eval golden datasets.
#
# WHAT IT DOES
#   Uploads ("seeds") curated golden rows to Braintrust so the eval runner has
#   data to score against (view at https://gai.airbnb.tools). Flow:
#     1. Pick a category (summaries, correlations, ...).
#     2. Multi-select the datasets within that category.
#     3. Confirm, then each dataset is uploaded and reports Done/Failed.
#   Categories and datasets are auto-discovered from the evals/ directory (any
#   subdir with a non-empty seed_dataset.py), so new datasets appear with no edits.
#   Rows are UPSERTED by reference_id: re-seeding is safe and only changes rows
#   whose input/expected/metadata you edited — existing rows stay stable.
#
# REQUIREMENTS
#   - gum  (brew install gum)        interactive prompts
#   - uv                             runs the seed modules
#   No IAP token or Facade access needed — seeding only writes to Braintrust,
#   and that auth is handled automatically by the genai-studio SDK.
#
# USAGE
#   cd matik/
#   ./scripts/seed_dataset.sh      (execute it — do NOT `source` it)
#
# NOTES
#   - No `set -e`: a single failed seed or cancelled prompt won't abort the
#     run or close your terminal.
#   - Each seed runs in a subshell, so your shell's working directory is
#     never changed by this script.

# Refuse to run when sourced: `set -uo pipefail` and the `exit` calls below
# would otherwise apply to (and silently kill) the caller's interactive shell.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    echo "Run this script directly, do not source it:  ./scripts/seed_dataset.sh" >&2
    return 1 2>/dev/null || exit 1
fi

set -uo pipefail

if ! command -v gum &>/dev/null; then
    echo "Error: gum is not installed. Install with: brew install gum"
    exit 1
fi

# The matik project root, so `python -m evals...` resolves regardless of the
# caller's working directory. This script lives at matik/scripts/seed_dataset.sh.
# We do NOT cd here — each uv command runs in a subshell that cds into PROJECT_ROOT,
# so the caller's shell directory is never changed (even if this script is sourced).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# List dataset categories (immediate subdirs of evals/ that hold dataset modules).
list_categories() {
    find "${PROJECT_ROOT}/evals" -mindepth 1 -maxdepth 1 -type d \
        ! -name "__pycache__" -exec basename {} \; | sort
}

# List seedable datasets in a category: subdirs containing a non-empty seed_dataset.py.
list_datasets() {
    local category=$1
    local d
    for d in "${PROJECT_ROOT}/evals/${category}"/*/; do
        [[ -s "${d}seed_dataset.py" ]] && basename "$d"
    done | sort
}

gum style \
    --foreground 212 --border-foreground 212 --border double \
    --align center --width 50 --margin "1 2" \
    "Matik Dataset Seeder"

gum style --foreground 214 \
    "  Seeding upserts golden rows into Braintrust by reference_id." \
    "  Re-seeding is safe; only changed rows are updated."

# Step 1: choose a category (summaries, correlations, ...).
gum style --foreground 240 "  Select a dataset category (ENTER to confirm)"
CATEGORY=$(list_categories | gum choose --header "Category:" || true)
if [[ -z "$CATEGORY" ]]; then
    echo "No category selected. Exiting."
    exit 0
fi

# Step 2: select datasets within the category. gum choose requires SPACE to tick
# items before Enter — if nothing is selected, re-prompt instead of exiting.
# (Built without `mapfile` so it runs on macOS's bundled bash 3.2.)
CATEGORY_DATASETS=()
while IFS= read -r _line; do
    CATEGORY_DATASETS+=("$_line")
done < <(list_datasets "$CATEGORY")
if [[ ${#CATEGORY_DATASETS[@]} -eq 0 ]]; then
    gum style --foreground 196 "  No seedable datasets found in '${CATEGORY}'. Exiting."
    exit 0
fi

SELECTED=""
while [[ -z "$SELECTED" ]]; do
    gum style --foreground 240 "  Use SPACE to select one or more, then ENTER to confirm"
    # `|| true` so an Esc/cancel doesn't trip pipefail.
    SELECTED=$(printf '%s\n' "${CATEGORY_DATASETS[@]}" | gum choose --no-limit --header "Select ${CATEGORY} datasets to seed:" || true)
    if [[ -z "$SELECTED" ]]; then
        gum style --foreground 196 "  Nothing selected — press SPACE to tick a dataset first."
        if ! gum confirm "Try again?"; then
            echo "No datasets selected. Exiting."
            exit 0
        fi
    fi
done

# Confirm before writing to Braintrust.
if ! gum confirm "Seed the selected dataset(s) to Braintrust?"; then
    echo "Cancelled."
    exit 0
fi

# Seed selected datasets.
while IFS= read -r dataset; do
    gum style --foreground 82 "Seeding dataset: $dataset"
    # Subshell cd so the caller's working directory is never changed.
    (cd "$PROJECT_ROOT" && uv run python -m "evals.${CATEGORY}.${dataset}.seed_dataset") \
        && gum style --foreground 82 "Done: $dataset" \
        || gum style --foreground 196 "Failed: $dataset"
done <<< "$SELECTED"

gum style \
    --foreground 212 --border-foreground 212 --border rounded \
    --align center --width 50 --margin "1 2" \
    "Seeding complete. View datasets at gai.airbnb.tools"
