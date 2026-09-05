"""Unit tests for the correlation harness in correlations/_common.py."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.models.enigmatologist_config import EnigmatologistConfig
from enigmatologist.correlations.reliability.incident.state import ChangeEvent
from evals import _common as harness
from evals.correlations import _common

_FAKE_KUBE_GEN = {
    "common": {
        "all": {
            "params": {
                "facade": {
                    "default_model": "matik-production-gpt-5",
                    "resource_bucket": "production",
                },
                "bedrock": {
                    "default_model": "global.anthropic.claude-sonnet-4-20250514-v1:0",
                },
                "enigmatologist": {
                    "llm_provider": "bedrock",
                    "correlation_system_prompt": "You are an incident correlation engine.",
                    "min_llm_score": 0.3,
                    # kube-gen stores this as a stringified list (must be parsed).
                    "speculative_patterns": "['\\bmay\\b', '\\bcould\\b']",
                    "speculative_max_score": 0.7,
                },
            }
        }
    }
}


@pytest.fixture
def fake_kube_gen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "load_kube_gen", lambda: _FAKE_KUBE_GEN)


_INPUT: dict[str, Any] = {
    "incident_description": "payments-gateway failing",
    "incident_created_at": "2026-05-10T14:30:00",
    "incident_affected_services": ["payments-gateway"],
    "github_events": [
        {
            "id": "PR-1",
            "description": "payments-gateway: change validation",
            "timestamp": "2026-05-10T13:55:00",
            "services": ["payments-gateway"],
        }
    ],
    "jira_events": [],
}


class TestLoadEnigmatologistConfig:
    def test_reads_from_kube_gen(self, fake_kube_gen: None) -> None:
        cfg = _common.load_enigmatologist_config()
        assert cfg.correlation_system_prompt.startswith("You are an incident")
        assert cfg.min_llm_score == 0.3
        # The stringified list from kube-gen is parsed into real regex strings.
        assert cfg.speculative_patterns == ["\\bmay\\b", "\\bcould\\b"]
        assert cfg.speculative_max_score == 0.7


class TestBuildState:
    def test_builds_change_events_with_parsed_times(self) -> None:
        state = _common.build_state(_INPUT)
        assert state["incident_created_at"] == datetime(2026, 5, 10, 14, 30)
        gh = state["github_events"]
        assert len(gh) == 1
        assert isinstance(gh[0], ChangeEvent)
        assert gh[0].id == "PR-1"
        assert gh[0].start_time == datetime(2026, 5, 10, 13, 55)
        assert state["jira_events"] == []

    def test_defaults_for_missing_fields(self) -> None:
        state = _common.build_state({"incident_created_at": "2026-05-10T14:30:00"})
        assert state["incident_description"] == ""
        assert state["incident_affected_services"] == []
        assert state["github_events"] == []


class TestLoadGenerator:
    def test_bedrock_provider_uses_bedrock_model(self, fake_kube_gen: None) -> None:
        factory, model, provider = _common.load_generator()
        assert provider == "bedrock"
        assert model == "global.anthropic.claude-sonnet-4-20250514-v1:0"
        assert callable(factory)

    def test_facade_provider_uses_facade_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade_kube_gen = {
            "common": {
                "all": {
                    "params": {
                        "facade": {
                            "default_model": "matik-production-gpt-5",
                            "resource_bucket": "production",
                        },
                        "enigmatologist": {"llm_provider": "facade"},
                    }
                }
            }
        }
        monkeypatch.setattr(harness, "load_kube_gen", lambda: facade_kube_gen)
        factory, model, provider = _common.load_generator()
        assert provider == "facade"
        assert model == "matik-production-gpt-5"
        assert callable(factory)


class TestMakeTask:
    def _factory(
        self, raw: str, tokens: tuple[int, int] = (10, 5)
    ) -> Callable[[], MagicMock]:
        client = MagicMock()
        client.send_message_with_usage = AsyncMock(return_value=(raw, *tokens))
        return lambda: client

    def _eg(self) -> EnigmatologistConfig:
        return EnigmatologistConfig(
            correlation_system_prompt="prompt",
            min_llm_score=0.3,
            speculative_patterns=[],
        )

    @pytest.fixture(autouse=True)
    def _patch_span(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        span = MagicMock()
        monkeypatch.setattr(_common, "current_span", lambda: span)
        return span

    def test_calls_llm_and_parses_selection(self) -> None:
        raw = '{"biztech_github": [{"id": "PR-1", "score": 0.8, "reasoning": "grounded"}], "jira": []}'
        task = _common.make_task(self._factory(raw), self._eg())
        result = task(_INPUT)
        assert result["raw"] == raw
        assert result["selected"] == [
            {"id": "PR-1", "score": 0.8, "reasoning": "grounded"}
        ]

    def test_filters_below_min_score(self) -> None:
        raw = '{"biztech_github": [{"id": "PR-1", "score": 0.1, "reasoning": "weak"}], "jira": []}'
        task = _common.make_task(self._factory(raw), self._eg())
        assert task(_INPUT)["selected"] == []

    def test_malformed_output_yields_empty_selection_but_keeps_raw(self) -> None:
        task = _common.make_task(self._factory("not json"), self._eg())
        result = task(_INPUT)
        assert result["selected"] == []
        assert result["raw"] == "not json"

    def test_logs_token_metrics(self, _patch_span: MagicMock) -> None:
        raw = '{"biztech_github": [], "jira": []}'
        task = _common.make_task(self._factory(raw, tokens=(12, 7)), self._eg())
        task(_INPUT)
        metrics = _patch_span.log.call_args.kwargs["metrics"]
        assert metrics["prompt_tokens"] == 12
        assert metrics["completion_tokens"] == 7
        assert metrics["tokens"] == 19


class TestRunEval:
    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr(_common, "init_sdk", MagicMock())
        monkeypatch.setattr(
            _common, "build_judge_client", lambda p, m: (MagicMock(), "judge-model")
        )
        monkeypatch.setattr(_common, "init_dataset", MagicMock(return_value=["row"]))
        eval_mock = MagicMock()
        monkeypatch.setattr(_common, "Eval", eval_mock)
        return eval_mock

    def test_registers_experiment_with_six_scorers(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch(monkeypatch)
        _common.run_eval(
            eval_slug="incident", dataset_name="correlation-incident-golden"
        )
        eval_mock.assert_called_once()
        kwargs = eval_mock.call_args.kwargs
        # precision, recall, f1, structure, calibration, reasoning
        assert len(kwargs["scores"]) == 6

    def test_category_tag_and_experiment_name(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch(monkeypatch)
        _common.run_eval(
            eval_slug="incident", dataset_name="correlation-incident-golden"
        )
        kwargs = eval_mock.call_args.kwargs
        assert "category:correlation" in kwargs["tags"]
        assert kwargs["experiment_name"].startswith("correlation-incident-")
        assert kwargs["metadata"]["category"] == "correlation"

    def test_generator_is_bedrock_and_judge_defaults_to_facade(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        eval_mock = self._patch(monkeypatch)
        _common.run_eval(
            eval_slug="incident", dataset_name="correlation-incident-golden"
        )
        kwargs = eval_mock.call_args.kwargs
        # Generator mirrors production (Bedrock/Claude).
        assert (
            kwargs["metadata"]["model"]
            == "global.anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert "model:global.anthropic.claude-sonnet-4-20250514-v1:0" in kwargs["tags"]
        # Judge defaults to the other family (Facade) to avoid self-bias.
        assert kwargs["metadata"]["judge_provider"] == "facade"
