"""Seed the github pr summary summary golden dataset into Braintrust.

The golden rows live in ``golden_dataset.yml`` next to this file (data separate from
code). The row schema is ``GoldenRow`` in :mod:`evals.summaries._common`, and the
load/seed logic is shared via :mod:`evals._common`.

⚠️  The data is FICTIONAL — see the header in ``golden_dataset.yml``.

Usage:
    cd matik/
    uv run python -m evals.summaries.github_pr_summary.seed_dataset
"""

from typing import cast

from evals._common import load_golden_rows, seed_golden_dataset
from evals.summaries._common import CATEGORY, GoldenRow

DATASET_NAME = f"{CATEGORY}-github-pr-summary-golden"
DATASET_DESCRIPTION = "Fictional golden dataset for the GitHub PR summary eval (PR title/description/comments -> changelog-style summary)."

GOLDEN_ROWS: list[GoldenRow] = cast("list[GoldenRow]", load_golden_rows(__file__))


def main() -> None:
    seed_golden_dataset(
        dataset_name=DATASET_NAME,
        description=DATASET_DESCRIPTION,
        category=CATEGORY,
        rows=GOLDEN_ROWS,
    )


if __name__ == "__main__":
    main()
