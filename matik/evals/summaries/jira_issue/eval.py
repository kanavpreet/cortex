"""Eval for Jira issue summaries.

Runs the production Jira issue-description prompt against the golden dataset and
scores results with LLM judges. Each run is a new experiment in Braintrust —
compare runs side-by-side to validate prompt changes.

Usage:
    cd matik/
    uv run python -m evals.summaries.jira_issue.eval --experiment "prompt-v1-baseline"
"""

from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = (
    "Focus on whether the summary states the core problem or request, the "
    "business impact, affected systems, and desired outcome of the Jira issue, "
    "grounded only in the issue description."
)


def main() -> None:
    run_eval(
        eval_slug="jira-issue",
        dataset_name=f"{CATEGORY}-jira-issue-golden",
        prompt_key="jira_description_prompt",
        input_keys=["issue_description"],
        operation="issue_summary",
        criteria=_CRITERIA,
    )


if __name__ == "__main__":
    main()
