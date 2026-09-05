"""Scorers for incident-correlation evals.

Correlation is a retrieval problem, not a generation one: given an incident and a
pool of candidate changes, the LLM selects which changes are causal. The correct
answer is objective (a set of IDs), so correctness is scored with deterministic
set-comparison metrics rather than an LLM judge:

- ``correlation_precision`` — of the changes selected, how many are truly causal.
- ``correlation_recall``    — of the truly-causal changes, how many were selected.
- ``correlation_f1``        — harmonic mean of precision and recall.

A single LLM-judge scorer (``reasoning_quality``) checks the one thing that is
genuinely subjective: whether the selected matches' reasoning is grounded in the
source and free of speculation.

Task output contract (what the eval task returns):
    {"selected": [{"id": str, "score": float, "reasoning": str}, ...]}
Expected (gold) contract (dataset ``expected``):
    {"correlated_ids": [str, ...]}
"""

import json
from collections.abc import Callable
from typing import Any

from braintrust import Score

from common.clients.bedrock_client import BedrockClientSync
from common.clients.facade_client import (
    FacadeClientSync,
    facade_system_message,
    facade_user_message,
)
from enigmatologist.correlations.reliability.incident.nodes import (
    parse_llm_correlations,
)

JudgeClient = FacadeClientSync | BedrockClientSync
Scorer = Callable[[dict[str, Any], dict[str, Any], dict[str, Any] | None], Score]


def _selected_ids(output: dict[str, Any] | None) -> set[str]:
    if not output:
        return set()
    return {m["id"] for m in output.get("selected", []) if m.get("id")}


def _gold_ids(expected: dict[str, Any] | None) -> set[str]:
    if not expected:
        return set()
    return set(expected.get("correlated_ids", []))


def make_precision_scorer() -> Scorer:
    """Precision = |selected ∩ gold| / |selected|.

    An empty selection scores 1.0 only when gold is also empty (correctly
    predicting "no correlations").
    """

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        selected = _selected_ids(output)
        gold = _gold_ids(expected)
        tp = len(selected & gold)
        value = tp / len(selected) if selected else (1.0 if not gold else 0.0)
        return Score(
            name="correlation_precision",
            score=value,
            metadata={"true_positives": tp, "selected": sorted(selected)},
        )

    score.__name__ = "correlation_precision"
    return score


def make_recall_scorer() -> Scorer:
    """Recall = |selected ∩ gold| / |gold|.

    Empty gold scores 1.0 only when nothing was selected (correctly predicting
    "no correlations").
    """

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        selected = _selected_ids(output)
        gold = _gold_ids(expected)
        tp = len(selected & gold)
        value = tp / len(gold) if gold else (1.0 if not selected else 0.0)
        return Score(
            name="correlation_recall",
            score=value,
            metadata={"true_positives": tp, "gold": sorted(gold)},
        )

    score.__name__ = "correlation_recall"
    return score


def make_f1_scorer() -> Scorer:
    """F1 = harmonic mean of precision and recall over selected vs gold IDs."""

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        selected = _selected_ids(output)
        gold = _gold_ids(expected)
        tp = len(selected & gold)
        precision = tp / len(selected) if selected else (1.0 if not gold else 0.0)
        recall = tp / len(gold) if gold else (1.0 if not selected else 0.0)
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        return Score(
            name="correlation_f1",
            score=f1,
            metadata={
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "false_positives": sorted(selected - gold),
                "false_negatives": sorted(gold - selected),
            },
        )

    score.__name__ = "correlation_f1"
    return score


def make_structure_scorer() -> Scorer:
    """Binary: does the raw LLM output match the required structure?

    Validity == the production parser (``parse_llm_correlations``) can consume it,
    so this catches the model drifting from the documented JSON contract
    (``{"biztech_github": [...], "jira": [...]}`` with id/score/reasoning items).
    Uses the shared parser so it never drifts from production.
    """

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        raw = (output or {}).get("raw")
        try:
            # min_score=0 + no patterns: validate shape only, no filtering.
            parse_llm_correlations(raw or "", 0.0, [])
            valid = True
        except Exception:
            valid = False
        return Score(
            name="output_structure",
            score=1.0 if valid else 0.0,
            metadata={"valid": valid},
        )

    score.__name__ = "output_structure"
    return score


def make_calibration_scorer() -> Scorer:
    """How well the LLM's confidence scores land in the expected band.

    Each gold row may declare ``expected.score_bands`` = ``{id: [lo, hi]}``. Of the
    selected matches that have a declared band, the fraction whose score falls in
    band. 1.0 when no selected match has a band (nothing to calibrate against).
    """

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        bands: dict[str, list[float]] = (expected or {}).get("score_bands", {})
        selected = (output or {}).get("selected", [])
        scored = [(m["id"], m.get("score")) for m in selected if m["id"] in bands]
        if not scored:
            return Score(
                name="score_calibration",
                score=1.0,
                metadata={"reasoning": "No selected match has an expected band."},
            )
        in_band = sum(
            1
            for _id, sc in scored
            if sc is not None and bands[_id][0] <= sc <= bands[_id][1]
        )
        return Score(
            name="score_calibration",
            score=in_band / len(scored),
            metadata={"in_band": in_band, "evaluated": len(scored)},
        )

    score.__name__ = "score_calibration"
    return score


_REASONING_SYSTEM_PROMPT = """You evaluate the reasoning quality of an incident-correlation engine.
The engine selected some recent changes (PRs/TCMRs) as causes of an incident, each with a one-sentence reasoning.
Score from 0.0 to 1.0 how well the reasonings are supported by the source content.

<Rubric>
A high score (approaching 1.0):
- Each reasoning cites a specific concrete component shared by the change and the incident (service, API, system, certificate, queue, etc.).
- Names a plausible mechanism linking the change to the incident. Hedging words ("could", "may", "might") are acceptable when a specific shared component is named — a grounded causal inference may be phrased tentatively. Treat hedging as speculation ONLY when there is no concrete shared component or mechanism behind it.
- No claims that contradict the provided descriptions (e.g. an "exact service match" claim must hold against the services listed below).

Lower the score for vague/generic justifications ("related deployment", "timing", "both involve X"), unsupported speculation, or claims not grounded in the source.
If no changes were selected, score 1.0 (nothing to justify).
</Rubric>

Respond with a JSON object only, no other text:
{"score": <float 0.0-1.0>, "reasoning": "<one sentence>"}"""


def _reasoning_user_content(input: dict[str, Any], output: dict[str, Any]) -> str:
    incident = input.get("incident_description", "")
    incident_services = input.get("incident_affected_services", []) or []
    candidates = (input.get("github_events", []) or []) + (
        input.get("jira_events", []) or []
    )
    # Include each candidate's services and the incident's affected services so the
    # judge can verify "exact service match" claims (it can't otherwise, and guesses).
    candidate_lines = "\n".join(
        f"- {c.get('id')} [services: {', '.join(c.get('services') or []) or 'none'}]: "
        f"{c.get('description', '')}"
        for c in candidates
    )
    selected = output.get("selected", []) if output else []
    selected_lines = "\n".join(
        f"- {m.get('id')} (score {m.get('score')}): {m.get('reasoning', '')}"
        for m in selected
    )
    return (
        f"Incident: {incident}\n"
        f"Incident affected services: {', '.join(incident_services) or 'none listed'}\n\n"
        f"Candidate changes:\n{candidate_lines or '(none)'}\n\n"
        f"Selected correlations and their reasoning:\n{selected_lines or '(none)'}"
    )


def make_reasoning_scorer(
    judge_client: JudgeClient, scorer_model: str | None = None
) -> Scorer:
    """LLM-judge scorer for whether selected matches' reasoning is grounded and
    non-speculative. Judges the output only; no gold needed."""

    def score(
        input: dict[str, Any],
        output: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> Score:
        if not output or not output.get("selected"):
            return Score(
                name="reasoning_quality",
                score=None,
                metadata={"reasoning": "No correlations selected; nothing to justify."},
            )
        try:
            response = judge_client.send_message_with_retry(
                model=scorer_model,
                messages=[
                    facade_system_message(_REASONING_SYSTEM_PROMPT),
                    facade_user_message(_reasoning_user_content(input, output)),
                ],
                operation="eval_correlation_reasoning",
            )
            result = json.loads(response)
            return Score(
                name="reasoning_quality",
                score=float(result.get("score", 0.0)),
                metadata={"reasoning": result.get("reasoning", "")},
            )
        except Exception as e:
            return Score(
                name="reasoning_quality", score=None, metadata={"error": str(e)}
            )

    score.__name__ = "reasoning_quality"
    return score
