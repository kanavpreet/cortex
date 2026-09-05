"""Unit tests for the summary-specific harness in summaries/_common.py.

Category-neutral plumbing (kube-gen access, judge client, naming, tags) is tested
in evals/_common_test.py. These cover the summary-specific pieces:
build_user_content, get_prompt_from_kube_gen, make_task, and run_eval wiring.
"""

from unittest.mock import MagicMock

import pytest

from evals import _common as harness
from evals.summaries import _common

# Fake kube-gen used by anything that resolves params (prompt, facade config).
# Patches the shared harness's loader, since that's what resolve_param calls.
_FAKE_KUBE_GEN = {
    "common": {
        "all": {
            "params": {
                "facade": {
                    "default_model": "matik-sandbox-gpt-5",
                    "resource_bucket": "production",
                },
                "enricher": {
                    "general_prompt": (
                        "Process the content:\n"
                        "1. PII Scrubbing: redact names.\n"
                        "2. Summary Generation: {source_instructions}\n"
                    ),
                    "source_mappings": {
                        "incidentio_description_prompt": "  You summarize incidents.\n  Keep it short.\n",
                    },
                },
            }
        },
        "production": {
            "params": {
                "facade": {
                    "default_model": "matik-production-gpt-5",
                },
            }
        },
    }
}


@pytest.fixture
def fake_kube_gen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "load_kube_gen", lambda: _FAKE_KUBE_GEN)


class TestBuildUserContent:
    """Tests for build_user_content (mirrors the enricher's formatting)."""

    def test_single_key(self) -> None:
        out = _common.build_user_content(
            {"original_description": "body"}, ["original_description"]
        )
        assert out == "Original Description:\nbody"

    def test_multiple_keys_joined_by_blank_line(self) -> None:
        out = _common.build_user_content(
            {"summary": "s", "resolution_statement": "r"},
            ["summary", "resolution_statement"],
        )
        assert out == "Summary:\ns\n\nResolution Statement:\nr"

    def test_missing_key_treated_as_empty(self) -> None:
        out = _common.build_user_content({"name": "n"}, ["name", "summary"])
        assert out == "Name:\nn\n\nSummary:\n"

    def test_only_requested_keys_used_in_order(self) -> None:
        out = _common.build_user_content(
            {"summary": "s", "name": "n"}, ["name", "summary"]
        )
        assert out == "Name:\nn\n\nSummary:\ns"


class TestGetPromptFromKubeGen:
    """Tests for get_prompt_from_kube_gen."""

    def test_assembles_general_prompt_with_source_instructions(
        self, fake_kube_gen: None
    ) -> None:
        # Mirrors EnricherConfig.build_prompt: source instructions are substituted
        # into the general_prompt template, so the PII directive is always included.
        prompt = _common.get_prompt_from_kube_gen("incidentio_description_prompt")
        assert prompt == (
            "Process the content:\n"
            "1. PII Scrubbing: redact names.\n"
            "2. Summary Generation: You summarize incidents.\nKeep it short."
        )

    def test_unknown_prompt_key_raises(self, fake_kube_gen: None) -> None:
        with pytest.raises(KeyError):
            _common.get_prompt_from_kube_gen("does_not_exist")


class TestMakeTask:
    """Tests for make_task (the Braintrust task closure)."""

    def test_calls_facade_and_returns_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade = MagicMock()
        facade.send_message_with_usage.return_value = ("the summary", 10, 5)
        span = MagicMock()
        monkeypatch.setattr(_common, "current_span", lambda: span)

        task = _common.make_task(facade, "sys prompt", ["name", "summary"], "op_label")
        result = task({"name": "n", "summary": "s"})

        assert result == "the summary"
        kwargs = facade.send_message_with_usage.call_args.kwargs
        assert kwargs["model"] is None
        assert kwargs["operation"] == "op_label"

    def test_logs_token_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = MagicMock()
        facade.send_message_with_usage.return_value = ("text", 10, 5)
        span = MagicMock()
        monkeypatch.setattr(_common, "current_span", lambda: span)

        _common.make_task(facade, "p", ["name"], "op")({"name": "n"})

        metrics = span.log.call_args.kwargs["metrics"]
        assert metrics["prompt_tokens"] == 10
        assert metrics["completion_tokens"] == 5
        assert metrics["tokens"] == 15


class TestRunEval:
    """Tests for run_eval — wiring of the Braintrust experiment (boundaries mocked)."""

    def _patch_boundaries(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Mock everything run_eval touches except the wiring logic; return the Eval mock."""
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(_common, "init_sdk", MagicMock())
        monkeypatch.setattr(_common, "create_facade_client_sync", MagicMock())
        monkeypatch.setattr(
            _common, "build_judge_client", lambda p, m: (MagicMock(), "judge-model")
        )
        monkeypatch.setattr(_common, "init_dataset", MagicMock(return_value=["row"]))
        eval_mock = MagicMock()
        monkeypatch.setattr(_common, "Eval", eval_mock)
        return eval_mock

    def _run(self) -> None:
        _common.run_eval(
            eval_slug="my-eval",
            dataset_name="my-golden",
            prompt_key="incidentio_description_prompt",
            input_keys=["name", "summary"],
            operation="op",
            criteria="crit",
        )

    def test_registers_one_experiment(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch_boundaries(monkeypatch)
        self._run()
        eval_mock.assert_called_once()
        assert eval_mock.call_args.kwargs["name"] == harness.PROJECT

    def test_experiment_name_starts_with_slug(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch_boundaries(monkeypatch)
        self._run()
        assert eval_mock.call_args.kwargs["experiment_name"].startswith(
            f"{_common.CATEGORY}-my-eval-"
        )

    def test_experiment_name_appends_suffix(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch_boundaries(monkeypatch)
        monkeypatch.setattr("sys.argv", ["prog", "--experiment", "baseline"])
        self._run()
        assert eval_mock.call_args.kwargs["experiment_name"].endswith("-baseline")

    def test_registers_three_scorers(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch_boundaries(monkeypatch)
        self._run()
        assert len(eval_mock.call_args.kwargs["scores"]) == 3

    def test_tags_and_metadata(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch_boundaries(monkeypatch)
        self._run()
        kwargs = eval_mock.call_args.kwargs
        assert "eval_slug:my-eval" in kwargs["tags"]
        assert f"category:{_common.CATEGORY}" in kwargs["tags"]
        assert kwargs["metadata"]["category"] == _common.CATEGORY
        assert kwargs["metadata"]["scorer_model"] == "judge-model"
        assert kwargs["metadata"]["prompt"]  # resolved from kube-gen
