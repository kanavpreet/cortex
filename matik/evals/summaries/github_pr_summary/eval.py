"""Eval for GitHub pull request summaries.

Runs the production PR-analyzer prompt against the golden dataset and scores
results with LLM judges. Each run is a new experiment in Braintrust — compare
runs side-by-side to validate prompt changes.

Usage:
    cd matik/
    uv run python -m evals.summaries.github_pr_summary.eval --experiment "prompt-v1-baseline"
"""

from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = (
    "Focus on whether the summary captures the purpose and impact of the pull "
    "request — key changes, affected components, motivation, and any notable "
    "concerns or decisions from the reviewer discussion — grounded only in the "
    "PR title, description, and comments."
)


def main() -> None:
    run_eval(
        eval_slug="github-pr-summary",
        dataset_name=f"{CATEGORY}-github-pr-summary-golden",
        prompt_key="ghe_pr_prompt",
        input_keys=["original_description"],
        operation="pull_request_summary",
        criteria=_CRITERIA,
    )


if __name__ == "__main__":
    main()
