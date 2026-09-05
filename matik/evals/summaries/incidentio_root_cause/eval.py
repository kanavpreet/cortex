"""Eval for incident root cause summaries.

Runs the production root-cause prompt against the golden dataset and scores
results with LLM judges. Each run is a new experiment in Braintrust — compare
runs side-by-side to validate prompt changes.

Usage:
    cd matik/
    uv run python -m evals.summaries.incidentio_root_cause.eval --experiment "prompt-v1-baseline"
"""

from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = (
    "Focus on whether the summary identifies the technical root cause, the "
    "affected systems, and contributing factors, grounded only in the incident "
    "summary and resolution statement."
)


def main() -> None:
    run_eval(
        eval_slug="incidentio-root-cause",
        dataset_name=f"{CATEGORY}-incidentio-root-cause-golden",
        prompt_key="incidentio_root_cause_prompt",
        input_keys=["summary", "resolution_statement"],
        operation="root_cause_summary",
        criteria=_CRITERIA,
    )


if __name__ == "__main__":
    main()
