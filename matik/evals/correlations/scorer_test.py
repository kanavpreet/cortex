"""Unit tests for the correlation scorers."""

import json
from typing import Any, cast

from braintrust import Score

from common.clients.facade_client import FacadeMessage
from evals.correlations.scorer import (
    JudgeClient,
    _reasoning_user_content,
    make_calibration_scorer,
    make_f1_scorer,
    make_precision_scorer,
    make_reasoning_scorer,
    make_recall_scorer,
    make_structure_scorer,
)

_VALID_RAW = (
    '{"biztech_github": [{"id": "PR-1", "score": 0.8, "reasoning": "r"}], "jira": []}'
)


class _FakeJudge:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        self.calls.append({"model": model, "operation": operation})
        return self._response


class _RaisingJudge:
    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        raise RuntimeError("boom")


def _out(*ids: str) -> dict[str, Any]:
    return {"selected": [{"id": i, "score": 0.7, "reasoning": "r"} for i in ids]}


def _gold(*ids: str) -> dict[str, Any]:
    return {"correlated_ids": list(ids)}


def _meta(result: Score) -> dict[str, object]:
    return cast(dict[str, object], result.metadata)


class TestPrecision:
    def test_partial(self) -> None:
        s = make_precision_scorer()({}, _out("A", "B"), _gold("A"))
        assert s.name == "correlation_precision"
        assert s.score == 0.5

    def test_perfect(self) -> None:
        assert make_precision_scorer()({}, _out("A"), _gold("A")).score == 1.0

    def test_empty_selection_empty_gold_is_one(self) -> None:
        assert make_precision_scorer()({}, _out(), _gold()).score == 1.0

    def test_empty_selection_with_gold_is_zero(self) -> None:
        assert make_precision_scorer()({}, _out(), _gold("A")).score == 0.0

    def test_handles_empty_output_and_expected(self) -> None:
        # Falsy output/expected exercise the helper guards; no selection, no gold.
        assert make_precision_scorer()({}, {}, {}).score == 1.0


class TestRecall:
    def test_partial(self) -> None:
        s = make_recall_scorer()({}, _out("A"), _gold("A", "B"))
        assert s.name == "correlation_recall"
        assert s.score == 0.5

    def test_empty_gold_empty_selection_is_one(self) -> None:
        assert make_recall_scorer()({}, _out(), _gold()).score == 1.0

    def test_empty_gold_with_selection_is_zero(self) -> None:
        assert make_recall_scorer()({}, _out("A"), _gold()).score == 0.0


class TestF1:
    def test_harmonic_mean(self) -> None:
        # selected {A,B}, gold {A,C}: precision 0.5, recall 0.5 -> f1 0.5
        s = make_f1_scorer()({}, _out("A", "B"), _gold("A", "C"))
        assert s.name == "correlation_f1"
        assert s.score == 0.5
        assert _meta(s)["false_positives"] == ["B"]
        assert _meta(s)["false_negatives"] == ["C"]

    def test_perfect(self) -> None:
        assert make_f1_scorer()({}, _out("A", "B"), _gold("A", "B")).score == 1.0

    def test_no_overlap_is_zero(self) -> None:
        assert make_f1_scorer()({}, _out("A"), _gold("B")).score == 0.0


class TestStructureScorer:
    def test_valid_raw_scores_one(self) -> None:
        s = make_structure_scorer()({}, {"selected": [], "raw": _VALID_RAW}, None)
        assert s.name == "output_structure"
        assert s.score == 1.0

    def test_valid_empty_arrays_scores_one(self) -> None:
        raw = '{"biztech_github": [], "jira": []}'
        assert (
            make_structure_scorer()({}, {"selected": [], "raw": raw}, None).score == 1.0
        )

    def test_malformed_raw_scores_zero(self) -> None:
        s = make_structure_scorer()({}, {"selected": [], "raw": "not json"}, None)
        assert s.score == 0.0

    def test_missing_raw_scores_zero(self) -> None:
        assert make_structure_scorer()({}, {"selected": []}, None).score == 0.0


class TestCalibrationScorer:
    def _bands(self, **b: list[float]) -> dict[str, Any]:
        return {"correlated_ids": list(b), "score_bands": b}

    def test_score_in_band(self) -> None:
        out = {"selected": [{"id": "PR-1", "score": 0.9, "reasoning": "r"}]}
        s = make_calibration_scorer()({}, out, self._bands(**{"PR-1": [0.8, 1.0]}))
        assert s.name == "score_calibration"
        assert s.score == 1.0

    def test_score_out_of_band(self) -> None:
        out = {"selected": [{"id": "PR-1", "score": 0.4, "reasoning": "r"}]}
        s = make_calibration_scorer()({}, out, self._bands(**{"PR-1": [0.8, 1.0]}))
        assert s.score == 0.0

    def test_partial(self) -> None:
        out = {
            "selected": [
                {"id": "PR-1", "score": 0.9, "reasoning": "r"},
                {"id": "PR-2", "score": 0.4, "reasoning": "r"},
            ]
        }
        bands = self._bands(**{"PR-1": [0.8, 1.0], "PR-2": [0.8, 1.0]})
        assert make_calibration_scorer()({}, out, bands).score == 0.5

    def test_no_bands_scores_one(self) -> None:
        out = {"selected": [{"id": "PR-1", "score": 0.9, "reasoning": "r"}]}
        assert make_calibration_scorer()({}, out, {"score_bands": {}}).score == 1.0


class TestReasoningScorer:
    def test_scores_judge_response(self) -> None:
        judge = _FakeJudge(json.dumps({"score": 0.9, "reasoning": "grounded"}))
        s = make_reasoning_scorer(cast(JudgeClient, judge))(
            {"incident_description": "x"}, _out("A"), None
        )
        assert s.name == "reasoning_quality"
        assert s.score == 0.9

    def test_empty_selection_is_unscored_without_judge(self) -> None:
        judge = _FakeJudge(json.dumps({"score": 0.0}))
        s = make_reasoning_scorer(cast(JudgeClient, judge))({}, _out(), None)
        assert s.score is None
        assert judge.calls == []

    def test_judge_failure_returns_none(self) -> None:
        s = make_reasoning_scorer(cast(JudgeClient, _RaisingJudge()))(
            {"incident_description": "x"}, _out("A"), None
        )
        assert s.score is None
        assert "error" in _meta(s)

    def test_prompt_includes_services_for_verification(self) -> None:
        # The judge must see both sides' services to verify "service match" claims.
        content = _reasoning_user_content(
            {
                "incident_description": "queue backing up",
                "incident_affected_services": ["orders-service"],
                "github_events": [
                    {"id": "PR-1", "description": "d", "services": ["queue-broker"]}
                ],
                "jira_events": [],
            },
            {"selected": [{"id": "PR-1", "score": 0.9, "reasoning": "r"}]},
        )
        assert "orders-service" in content
        assert "queue-broker" in content
