"""Eval for incident correlation.

Runs the production correlation prompt (the ``assign_correlations_by_llm`` node)
against a fixed candidate pool and scores which changes it selects with
precision/recall/F1 plus a reasoning-quality judge. Each run is a new experiment
in Braintrust — compare runs to validate prompt or model changes.

Usage:
    cd matik/
    uv run python -m evals.correlations.incident.eval --experiment "prompt-v2"

The experiment name always starts "<category>-<eval>-<timestamp>"; --experiment
adds an optional suffix describing what changed.
"""

from evals.correlations._common import CATEGORY, run_eval

_EVAL_SLUG = "incident"


def main() -> None:
    run_eval(
        eval_slug=_EVAL_SLUG,
        dataset_name=f"{CATEGORY}-{_EVAL_SLUG}-golden",
    )


if __name__ == "__main__":
    main()
