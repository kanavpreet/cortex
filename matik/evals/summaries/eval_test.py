"""Tests that each summary eval's main() wires run_eval correctly.

The eval.py modules are thin wrappers: main() calls run_eval(...) with this eval's
config. These tests run main() with run_eval mocked, so they verify the wiring
without making any LLM/Braintrust calls.
"""

import importlib
from unittest.mock import patch

import pytest

_REQUIRED_KWARGS = {
    "eval_slug",
    "dataset_name",
    "prompt_key",
    "input_keys",
    "operation",
    "criteria",
}

_EVAL_MODULES = [
    "incidentio_description",
    "incidentio_root_cause",
    "github_pr_summary",
    "jira_issue",
    "jira_comments",
]


@pytest.mark.parametrize("module", _EVAL_MODULES)
def test_main_calls_run_eval_once(module: str) -> None:
    mod = importlib.import_module(f"evals.summaries.{module}.eval")
    with patch.object(mod, "run_eval") as run_eval:
        mod.main()
    run_eval.assert_called_once()


@pytest.mark.parametrize("module", _EVAL_MODULES)
def test_main_passes_all_required_config(module: str) -> None:
    mod = importlib.import_module(f"evals.summaries.{module}.eval")
    with patch.object(mod, "run_eval") as run_eval:
        mod.main()
    kwargs = run_eval.call_args.kwargs

    assert set(kwargs) >= _REQUIRED_KWARGS
    assert kwargs["eval_slug"] and isinstance(kwargs["eval_slug"], str)
    assert kwargs["dataset_name"] and isinstance(kwargs["dataset_name"], str)
    assert kwargs["prompt_key"] and isinstance(kwargs["prompt_key"], str)
    assert kwargs["input_keys"] and isinstance(kwargs["input_keys"], list)
    assert kwargs["operation"] and isinstance(kwargs["operation"], str)
    assert kwargs["criteria"] and isinstance(kwargs["criteria"], str)
