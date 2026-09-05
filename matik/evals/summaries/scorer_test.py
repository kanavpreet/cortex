"""Unit tests for the shared LLM-as-judge scorers."""

import json
from typing import cast

from braintrust import Score

from common.clients.facade_client import FacadeMessage
from evals.summaries.scorer import (
    JudgeClient,
    _parse_judge_json,
    _run_judge,
    _source_text,
    make_faithfulness_scorer,
    make_pii_safe_scorer,
    make_quality_scorer,
)


class _FakeJudge:
    """Stand-in judge client that returns a canned response and records the call."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        self.calls.append(
            {"model": model, "messages": messages, "operation": operation}
        )
        return self._response


class _RaisingJudge:
    """Judge client that always raises, to exercise the failure path."""

    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        raise RuntimeError("boom")


def _as_judge(fake: object) -> JudgeClient:
    """Cast a fake to the JudgeClient union (duck-typed in production)."""
    return cast(JudgeClient, fake)


def _quality_response(score: float, reasoning: str = "ok") -> str:
    return json.dumps({"score": score, "reasoning": reasoning})


def _pii_response(pii_present: bool, reasoning: str = "ok") -> str:
    return json.dumps({"pii_present": pii_present, "reasoning": reasoning})


def _meta(result: Score) -> dict[str, object]:
    """Score.metadata as a plain dict for assertions (mypy-friendly)."""
    return cast(dict[str, object], result.metadata)


def _last_user_content(judge: _FakeJudge) -> str:
    messages = cast(list[FacadeMessage], judge.calls[0]["messages"])
    return messages[-1].content


class TestParseJudgeJson:
    """Judge responses must parse through markdown fences and surrounding prose."""

    def test_bare_json(self) -> None:
        assert _parse_judge_json('{"score": 0.8}') == {"score": 0.8}

    def test_json_fence(self) -> None:
        wrapped = '```json\n{"score": 0.8, "reasoning": "ok"}\n```'
        assert _parse_judge_json(wrapped) == {"score": 0.8, "reasoning": "ok"}

    def test_plain_fence(self) -> None:
        assert _parse_judge_json('```\n{"score": 0.5}\n```') == {"score": 0.5}

    def test_surrounding_prose(self) -> None:
        resp = 'Here is my evaluation:\n{"score": 0.3, "reasoning": "x"}\nThanks!'
        assert _parse_judge_json(resp) == {"score": 0.3, "reasoning": "x"}

    def test_unparseable_raises(self) -> None:
        import pytest

        with pytest.raises(json.JSONDecodeError):
            _parse_judge_json("no json here")


def test_run_judge_handles_fenced_response() -> None:
    """A fenced judge response is scored, not dropped to None (the null-score bug)."""
    judge = _FakeJudge('```json\n{"score": 0.9, "reasoning": "grounded"}\n```')
    result = _run_judge("faithfulness", _as_judge(judge), None, "sys", "user", "op")
    assert result.score == 0.9
    assert _meta(result)["reasoning"] == "grounded"


class TestSourceText:
    """Tests for _source_text field rendering."""

    def test_renders_labeled_sections(self) -> None:
        out = _source_text({"name": "Outage", "summary": "It broke"})
        assert out == "Name:\nOutage\n\nSummary:\nIt broke"

    def test_titlecases_underscored_keys(self) -> None:
        out = _source_text({"resolution_statement": "Fixed it"})
        assert out.startswith("Resolution Statement:\n")

    def test_single_field(self) -> None:
        assert _source_text({"original_description": "x"}) == "Original Description:\nx"


class TestRunJudge:
    """Tests for the _run_judge helper."""

    def test_parses_score_and_reasoning(self) -> None:
        judge = _FakeJudge(_quality_response(0.8, "good enough"))
        result = _run_judge(
            "summary_quality", _as_judge(judge), None, "sys", "user", "op"
        )
        assert isinstance(result, Score)
        assert result.score == 0.8
        assert _meta(result)["reasoning"] == "good enough"

    def test_passes_model_and_operation(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        _run_judge(
            "faithfulness", _as_judge(judge), "claude-x", "sys", "user", "eval_op"
        )
        assert judge.calls[0]["model"] == "claude-x"
        assert judge.calls[0]["operation"] == "eval_op"

    def test_missing_score_defaults_to_zero(self) -> None:
        judge = _FakeJudge(json.dumps({"reasoning": "no score key"}))
        result = _run_judge("summary_quality", _as_judge(judge), None, "s", "u", "op")
        assert result.score == 0.0

    def test_invalid_json_returns_none_score(self) -> None:
        judge = _FakeJudge("not json at all")
        result = _run_judge("summary_quality", _as_judge(judge), None, "s", "u", "op")
        assert result.score is None
        assert "error" in _meta(result)

    def test_judge_exception_returns_none_score(self) -> None:
        result = _run_judge(
            "summary_quality", _as_judge(_RaisingJudge()), None, "s", "u", "op"
        )
        assert result.score is None
        assert "boom" in str(_meta(result)["error"])


class TestQualityScorer:
    """Tests for make_quality_scorer."""

    def test_scores_against_gold(self) -> None:
        scorer = make_quality_scorer(
            _as_judge(_FakeJudge(_quality_response(0.9))), "criteria"
        )
        result = scorer({"name": "x", "summary": "y"}, "a summary", "gold")
        assert result.name == "summary_quality"
        assert result.score == 0.9

    def test_empty_output_scores_zero_without_calling_judge(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        scorer = make_quality_scorer(_as_judge(judge), "criteria")
        result = scorer({"summary": "y"}, "", "gold")
        assert result.score == 0.0
        assert judge.calls == []

    def test_missing_expected_scores_zero(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        scorer = make_quality_scorer(_as_judge(judge), "criteria")
        result = scorer({"summary": "y"}, "a summary", None)
        assert result.score == 0.0
        assert judge.calls == []

    def test_prompt_includes_criteria_source_and_gold(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        scorer = make_quality_scorer(_as_judge(judge), "focus on impact")
        scorer({"summary": "the source"}, "the output", "the gold")
        user_msg = _last_user_content(judge)
        assert "focus on impact" in user_msg
        assert "the source" in user_msg
        assert "the gold" in user_msg
        assert "the output" in user_msg

    def test_scorer_function_name(self) -> None:
        scorer = make_quality_scorer(_as_judge(_FakeJudge(_quality_response(1.0))), "c")
        assert scorer.__name__ == "summary_quality"


class TestFaithfulnessScorer:
    """Tests for make_faithfulness_scorer."""

    def test_scores_grounding(self) -> None:
        scorer = make_faithfulness_scorer(_as_judge(_FakeJudge(_quality_response(0.7))))
        result = scorer({"summary": "src"}, "an output", None)
        assert result.name == "faithfulness"
        assert result.score == 0.7

    def test_does_not_require_expected(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        scorer = make_faithfulness_scorer(_as_judge(judge))
        result = scorer({"summary": "src"}, "an output", None)
        assert result.score == 1.0
        assert len(judge.calls) == 1

    def test_empty_output_scores_zero(self) -> None:
        judge = _FakeJudge(_quality_response(1.0))
        scorer = make_faithfulness_scorer(_as_judge(judge))
        result = scorer({"summary": "src"}, "", None)
        assert result.score == 0.0
        assert judge.calls == []

    def test_scorer_function_name(self) -> None:
        scorer = make_faithfulness_scorer(_as_judge(_FakeJudge(_quality_response(1.0))))
        assert scorer.__name__ == "faithfulness"


class TestPiiSafeScorer:
    """Tests for make_pii_safe_scorer (binary)."""

    def test_clean_output_scores_one(self) -> None:
        scorer = make_pii_safe_scorer(_as_judge(_FakeJudge(_pii_response(False))))
        result = scorer({"summary": "src"}, "no pii here", None)
        assert result.name == "pii_safe"
        assert result.score == 1.0

    def test_leak_scores_zero(self) -> None:
        scorer = make_pii_safe_scorer(_as_judge(_FakeJudge(_pii_response(True))))
        result = scorer({"summary": "src"}, "contains jane@example.test", None)
        assert result.score == 0.0

    def test_is_binary_never_intermediate(self) -> None:
        for present in (True, False):
            scorer = make_pii_safe_scorer(_as_judge(_FakeJudge(_pii_response(present))))
            result = scorer({"summary": "src"}, "output", None)
            assert result.score in (0.0, 1.0)

    def test_empty_output_is_safe_without_calling_judge(self) -> None:
        judge = _FakeJudge(_pii_response(True))
        scorer = make_pii_safe_scorer(_as_judge(judge))
        result = scorer({"summary": "src"}, "", None)
        assert result.score == 1.0
        assert judge.calls == []

    def test_judge_failure_returns_none_score(self) -> None:
        scorer = make_pii_safe_scorer(_as_judge(_RaisingJudge()))
        result = scorer({"summary": "src"}, "output", None)
        assert result.score is None
        assert "error" in _meta(result)

    def test_scorer_function_name(self) -> None:
        scorer = make_pii_safe_scorer(_as_judge(_FakeJudge(_pii_response(False))))
        assert scorer.__name__ == "pii_safe"
