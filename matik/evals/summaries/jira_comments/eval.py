"""Eval for Jira issue comments summaries.

Runs the production Jira comments prompt against the golden dataset and scores
results with LLM judges. Each run is a new experiment in Braintrust — compare
runs side-by-side to validate prompt changes.

Usage:
    cd matik/
    uv run python -m evals.summaries.jira_comments.eval --experiment "prompt-v1-baseline"
"""

from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = (
    "Focus on whether the summary captures the key decisions, blockers "
    "identified, and current status from the Jira comment thread, grounded only "
    "in the comments."
)


def main() -> None:
    run_eval(
        eval_slug="jira-comments",
        dataset_name=f"{CATEGORY}-jira-comments-golden",
        prompt_key="jira_comments_prompt",
        input_keys=["aggregated_comments"],
        operation="issue_comments_summary",
        criteria=_CRITERIA,
    )


if __name__ == "__main__":
    main()
