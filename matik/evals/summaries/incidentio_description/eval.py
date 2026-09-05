"""Eval for incident description summaries.

Runs the production prompt against the golden dataset and scores results
with an LLM judge. Each run is a new experiment in Braintrust — compare
runs side-by-side to validate prompt changes.

Usage:
    cd matik/
    uv run python -m evals.summaries.incidentio_description.eval --experiment "prompt-v2"

The experiment name always starts "<category>-<eval>-<timestamp>"; --experiment
adds an optional suffix describing what changed, e.g. "add-business-impact".
"""

from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = (
    "Focus on whether the summary covers affected systems, nature of failure, "
    "and business impact in 2-3 concise factual sentences."
)


def main() -> None:
    run_eval(
        eval_slug="incidentio-description",
        dataset_name=f"{CATEGORY}-incidentio-description-golden",
        prompt_key="incidentio_description_prompt",
        input_keys=["name", "summary"],
        operation="description_summary",
        criteria=_CRITERIA,
    )


if __name__ == "__main__":
    main()
