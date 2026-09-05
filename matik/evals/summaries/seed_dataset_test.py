"""Structural tests for the golden seed datasets.

These validate the curated rows themselves (not Braintrust I/O): every summary
type should have a consistent, de-duplicated set of rows whose gold `expected`
summaries never leak PII. They do not call any network.
"""

import importlib
import re
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from evals import _common as harness

# (module under evals.summaries, the input field(s) the enricher sends)
_DATASETS = [
    ("incidentio_description", ["name", "summary"]),
    ("incidentio_root_cause", ["summary", "resolution_statement"]),
    ("github_pr_summary", ["original_description"]),
    ("jira_issue", ["issue_description"]),
    ("jira_comments", ["aggregated_comments"]),
]

# Fake personal names planted in the fictional inputs (must never reach expected).
_PLANTED_NAMES = [
    "Priya Raman",
    "Marcus Feldt",
    "Dana Whitlock",
    "Lena Ortiz",
    "Sam Okafor",
    "Jordan Maelis",
]
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _load(module: str) -> list[dict[str, object]]:
    mod = importlib.import_module(f"evals.summaries.{module}.seed_dataset")
    rows: list[dict[str, object]] = mod.GOLDEN_ROWS
    return rows


def _input_text(row: dict[str, object]) -> str:
    input_dict = cast(dict[str, str], row["input"])
    return " ".join(str(v) for v in input_dict.values())


@pytest.mark.parametrize("module,input_keys", _DATASETS)
class TestSeedDataset:
    def test_has_twenty_rows(self, module: str, input_keys: list[str]) -> None:
        assert len(_load(module)) == 20

    def test_reference_ids_unique(self, module: str, input_keys: list[str]) -> None:
        ids = [
            cast(dict[str, str], r["metadata"])["reference_id"] for r in _load(module)
        ]
        assert len(ids) == len(set(ids))

    def test_all_expected_populated(self, module: str, input_keys: list[str]) -> None:
        assert all(str(r["expected"]).strip() for r in _load(module))

    def test_input_keys_match_enricher(
        self, module: str, input_keys: list[str]
    ) -> None:
        for row in _load(module):
            assert list(cast(dict[str, str], row["input"]).keys()) == input_keys

    def test_expected_has_no_planted_names(
        self, module: str, input_keys: list[str]
    ) -> None:
        for row in _load(module):
            for name in _PLANTED_NAMES:
                assert name not in str(row["expected"]), f"{module} leaks name {name!r}"

    def test_expected_has_no_emails(self, module: str, input_keys: list[str]) -> None:
        for row in _load(module):
            assert not _EMAIL_RE.search(str(row["expected"])), (
                f"{module} leaks an email"
            )


@pytest.mark.parametrize("module,input_keys", _DATASETS)
def test_main_seeds_every_row(module: str, input_keys: list[str]) -> None:
    """main() should upsert every golden row and flush (I/O mocked in the shared helper)."""
    mod = importlib.import_module(f"evals.summaries.{module}.seed_dataset")
    dataset = MagicMock()
    with (
        patch.object(harness, "init_sdk"),
        patch.object(harness, "init_dataset", return_value=dataset) as init_dataset,
    ):
        mod.main()

    init_dataset.assert_called_once()
    kwargs = init_dataset.call_args.kwargs
    assert kwargs["name"] == mod.DATASET_NAME
    assert kwargs["metadata"]["category"] == "summary"
    assert kwargs["description"]
    assert dataset.insert.call_count == len(mod.GOLDEN_ROWS)
    dataset.flush.assert_called_once()


@pytest.mark.parametrize("module,input_keys", _DATASETS)
def test_at_least_one_pii_example_in_inputs(module: str, input_keys: list[str]) -> None:
    """Each dataset should exercise pii_safe: at least one input has planted PII."""
    rows = _load(module)
    has_pii = any(
        any(name in _input_text(r) for name in _PLANTED_NAMES)
        or _EMAIL_RE.search(_input_text(r))
        or "[~" in _input_text(r)
        for r in rows
    )
    assert has_pii, f"{module} has no PII example in any input"
