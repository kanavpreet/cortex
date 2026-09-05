#!/usr/bin/env bash
#
# run_eval.sh — Interactive runner for Matik LLM evals.
#
# WHAT IT DOES
#   Walks you through running one or more evals and uploads each run to
#   Braintrust as a new experiment (view at https://gai.airbnb.tools).
#   Flow:
#     1. Pick a category (summaries, correlations, ...).
#     2. Multi-select the evals within that category.
#     3. Optionally add an experiment description.
#     4. Pick the LLM judge: provider (bedrock/facade) and model.
#     5. A fresh IAP token is fetched automatically.
#     6. Each selected eval runs and reports Done/Failed.
#   Categories and evals are auto-discovered from the evals/ directory (any
#   subdir with a non-empty eval.py), so newly added evals appear with no edits.
#
# REQUIREMENTS
#   - gum  (brew install gum)        interactive prompts
#   - iap-auth                       fetches the IAP token for Facade/Bedrock
#   - uv                             runs the eval modules
#   The judge model defaults to Bedrock Claude Opus 4.8; the generator model is
#   read from kube-gen.yml by the eval itself (no flag needed here).
#
# USAGE
#   cd matik/
#   ./scripts/run_eval.sh
#
# NOTES
#   - No `set -e`: a single failed eval or cancelled prompt won't abort the
#     run or close your terminal.
#   - Each eval runs in a subshell, so your shell's working directory is
#     never changed by this script.

# Refuse to run when sourced: `set -uo pipefail` and the `exit` calls below
# would otherwise apply to (and silently kill) the caller's interactive shell.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    echo "Run this script directly, do not source it:  ./scripts/run_eval.sh" >&2
    return 1 2>/dev/null || exit 1
fi

set -uo pipefail

if ! command -v gum &>/dev/null; then
    echo "Error: gum is not installed. Install with: brew install gum"
    exit 1
fi

# The matik project root, so `python -m evals...` resolves regardless of the
# caller's working directory. This script lives at matik/scripts/run_eval.sh.
# We do NOT cd here — each uv command runs in a subshell that cds into PROJECT_ROOT,
# so the caller's shell directory is never changed (even if this script is sourced).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# List eval categories (immediate subdirs of evals/ that hold eval modules).
list_categories() {
    find "${PROJECT_ROOT}/evals" -mindepth 1 -maxdepth 1 -type d \
        ! -name "__pycache__" -exec basename {} \; | sort
}

# List runnable evals in a category: subdirs containing a non-empty eval.py.
list_evals() {
    local category=$1
    local d
    for d in "${PROJECT_ROOT}/evals/${category}"/*/; do
        [[ -s "${d}eval.py" ]] && basename "$d"
    done | sort
}

gum style \
    --foreground 212 --border-foreground 212 --border double \
    --align center --width 50 --margin "1 2" \
    "Matik Eval Runner"

# Step 1: choose a category (summaries, correlations, ...).
gum style --foreground 240 "  Select an eval category (ENTER to confirm)"
CATEGORY=$(list_categories | gum choose --header "Category:" || true)
if [[ -z "$CATEGORY" ]]; then
    echo "No category selected. Exiting."
    exit 0
fi

# Step 2: select evals within the category. gum choose requires SPACE to tick
# items before Enter — if nothing is selected, re-prompt instead of exiting.
# (Built without `mapfile` so it runs on macOS's bundled bash 3.2.)
CATEGORY_EVALS=()
while IFS= read -r _line; do
    CATEGORY_EVALS+=("$_line")
done < <(list_evals "$CATEGORY")
if [[ ${#CATEGORY_EVALS[@]} -eq 0 ]]; then
    gum style --foreground 196 "  No runnable evals found in '${CATEGORY}'. Exiting."
    exit 0
fi

SELECTED=""
while [[ -z "$SELECTED" ]]; do
    gum style --foreground 240 "  Use SPACE to select one or more, then ENTER to confirm"
    # `|| true` so an Esc/cancel doesn't trip pipefail.
    SELECTED=$(printf '%s\n' "${CATEGORY_EVALS[@]}" | gum choose --no-limit --header "Select ${CATEGORY} evals to run:" || true)
    if [[ -z "$SELECTED" ]]; then
        gum style --foreground 196 "  Nothing selected — press SPACE to tick an eval first."
        if ! gum confirm "Try again?"; then
            echo "No evals selected. Exiting."
            exit 0
        fi
    fi
done

# Optional description (Esc/cancel just leaves it empty).
DESCRIPTION=$(gum input --placeholder "Experiment description (optional, press Enter to skip)" || true)

# Choose the LLM judge provider. "default" lets the eval pick the judge family that
# avoids self-bias for its category (Bedrock/Claude for summaries, Facade/GPT for
# correlations) — recommended unless you specifically want to override it.
gum style --foreground 240 "  Select the LLM judge provider (ENTER to confirm)"
gum style --foreground 240 \
    "  'default' picks the right judge family per eval (recommended)."
JUDGE_PROVIDER=$(printf '%s\n' "default" "bedrock" "facade" | gum choose --header "Judge provider:" || true)
JUDGE_PROVIDER=${JUDGE_PROVIDER:-default}

# Common Bedrock judge models (aliases resolved by the eval). "default" and
# "custom..." let you keep the provider default or type any model id.
BEDROCK_MODELS=(
    "default"
    "claude-opus-4-8"
    "claude-opus-4-7"
    "claude-opus-4-5"
    "claude-sonnet-4-6"
    "claude-sonnet-4-5"
    "claude-haiku-4-5"
    "llama-3-3-70b"
    "llama-3-1-405b"
    "custom..."
)

# Facade judge models — the per-environment Facade models from kube-gen.yml.
FACADE_MODELS=(
    "default"
    "matik-production-gpt-5"
    "matik-staging-gpt-5"
    "matik-sandbox-gpt-5"
    "custom..."
)

JUDGE_MODEL=""
if [[ "$JUDGE_PROVIDER" == "bedrock" ]]; then
    gum style --foreground 240 "  Select the judge model (ENTER to confirm)"
    CHOICE=$(printf '%s\n' "${BEDROCK_MODELS[@]}" | gum choose --header "Judge model:" || true)
    if [[ "$CHOICE" == "custom..." ]]; then
        # Guide the user to the model catalog and teach the global-region format.
        gum style --foreground 39 \
            "  Browse model IDs: https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html"
        gum style --foreground 240 \
            "  Format: global.<provider>.<model>  (always use the global region for ~10% lower cost)" \
            "  e.g. global.anthropic.claude-opus-4-8  |  global.meta.llama3-3-70b-instruct-v1:0"
        JUDGE_MODEL=$(gum input --value "global." --placeholder "global.<provider>.<model>" || true)
        # Nudge toward global if they didn't use it.
        if [[ -n "$JUDGE_MODEL" && "$JUDGE_MODEL" != global.* ]]; then
            gum style --foreground 214 \
                "  Note: '$JUDGE_MODEL' is not a global-region id; consider prefixing 'global.' for lower cost."
        fi
    elif [[ -n "$CHOICE" && "$CHOICE" != "default" ]]; then
        JUDGE_MODEL="$CHOICE"
    fi
elif [[ "$JUDGE_PROVIDER" == "facade" ]]; then
    gum style --foreground 240 "  Select the judge model (ENTER to confirm)"
    CHOICE=$(printf '%s\n' "${FACADE_MODELS[@]}" | gum choose --header "Judge model:" || true)
    if [[ "$CHOICE" == "custom..." ]]; then
        JUDGE_MODEL=$(gum input --placeholder "Facade model id" || true)
    elif [[ -n "$CHOICE" && "$CHOICE" != "default" ]]; then
        JUDGE_MODEL="$CHOICE"
    fi
fi
# JUDGE_PROVIDER == "default": pass nothing; the eval picks its category default.

# Both the generator (Facade) and the local Bedrock judge need an IAP token.
# IAP tokens are short-lived (~1h), so always fetch a fresh one rather than reuse a
# possibly-stale value already in the environment. Fall back to the existing token
# only if the refresh fails.
gum style --foreground 240 "  Fetching a fresh IAP_TOKEN via iap-auth..."
if FRESH_IAP_TOKEN=$(iap-auth https://llm-fusion-hub.a.musta.ch); then
    export IAP_TOKEN="$FRESH_IAP_TOKEN"
    gum style --foreground 82 "  IAP_TOKEN refreshed."
elif [[ -n "${IAP_TOKEN:-}" ]]; then
    gum style --foreground 214 "  iap-auth failed — reusing existing IAP_TOKEN (may be stale)."
else
    gum style --foreground 196 "  Failed to fetch IAP_TOKEN — LLM calls will likely fail."
    export IAP_TOKEN=""
fi

# Run selected evals
while IFS= read -r eval_name; do
    gum style --foreground 82 "Running eval: $eval_name"

    ARGS=()
    if [[ -n "$DESCRIPTION" ]]; then
        ARGS+=(--description "$DESCRIPTION")
    fi
    # "default" -> pass nothing so the eval picks its category-appropriate judge.
    if [[ "$JUDGE_PROVIDER" != "default" ]]; then
        ARGS+=(--judge-provider "$JUDGE_PROVIDER")
        if [[ -n "$JUDGE_MODEL" ]]; then
            ARGS+=(--judge-model "$JUDGE_MODEL")
        fi
    fi

    # Subshell cd so the caller's working directory is never changed.
    # ${ARGS[@]+...} guards the empty-array expansion (bash 3.2 + set -u errors otherwise).
    (cd "$PROJECT_ROOT" && uv run python -m "evals.${CATEGORY}.${eval_name}.eval" ${ARGS[@]+"${ARGS[@]}"}) \
        && gum style --foreground 82 "Done: $eval_name" \
        || gum style --foreground 196 "Failed: $eval_name"

done <<< "$SELECTED"

gum style \
    --foreground 212 --border-foreground 212 --border rounded \
    --align center --width 50 --margin "1 2" \
    "All evals complete. View results at gai.airbnb.tools"
