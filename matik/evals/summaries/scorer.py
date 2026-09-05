"""Shared LLM-as-judge scorers for all summary eval types.

Three scorers, each a separate Braintrust score column. All scored so HIGHER IS
BETTER (Braintrust convention) — columns are named after the positive property,
not the failure mode, so 1.0 always reads as "good":

- ``summary_quality`` — factual accuracy, completeness, conciseness vs the gold summary (0.0-1.0).
- ``faithfulness``    — 1.0 = every claim grounded in the source, 0.0 = fabricated detail (0.0-1.0).
- ``pii_safe``        — binary: 1.0 = no PII in the output, 0.0 = leaks identifying info.

The faithfulness and PII rubrics are adapted from the LangSmith correctness
prompts, tailored for incident/ticket summaries.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from braintrust import Score

from common.clients.bedrock_client import BedrockClientSync
from common.clients.facade_client import (
    FacadeClientSync,
    facade_system_message,
    facade_user_message,
)

# The judge can run on Facade (GPT) or Bedrock (Claude). Both sync clients expose
# the same send_message_with_retry signature, so the scorers treat them uniformly.
JudgeClient = FacadeClientSync | BedrockClientSync

# A scorer takes (input, output, expected) and returns a Braintrust Score, which
# carries both the numeric score and the judge's reasoning (in metadata) so the
# justification is visible in the Braintrust UI per the eval-guide rules.
Scorer = Callable[[dict[str, str], str, str | None], Score]

_JSON_OUTPUT_INSTRUCTION = (
    "Respond with a JSON object only, no other text:\n"
    '{"score": <float 0.0-1.0>, "reasoning": "<one sentence>"}'
)

_QUALITY_SYSTEM_PROMPT = f"""You are an expert evaluator of AI-generated summaries for technical operations content (incidents, tickets, pull requests).
You will be given type-specific criteria, the original source content, an AI-generated summary, and an expected (gold) summary.
Score the AI-generated summary from 0.0 to 1.0 on overall quality against the gold summary and criteria.

<Rubric>
A high-quality summary (approaching 1.0):
- Factual accuracy: every statement accurately reflects the source content
- Completeness: covers the key points present in the expected (gold) summary
- Conciseness: appropriately brief, with no padding and no critical omissions
- Satisfies the type-specific criteria provided below

Lower the score as the summary misses key points, adds inaccuracies, or becomes verbose.
A score of 0.0 means the summary is largely inaccurate, irrelevant, or unusable.
The expected summary is the reference for "good" — reward summaries that match its substance,
not its exact wording (faithful paraphrases are fine).
</Rubric>

<Instructions>
- Read the criteria, source content, and expected summary carefully.
- Compare the AI-generated summary against the expected summary on accuracy, completeness, and conciseness.
- Judge meaning, not phrasing — do not penalize wording differences that preserve meaning.
- Weight the type-specific criteria when deciding the score.
</Instructions>

{_JSON_OUTPUT_INSTRUCTION}"""

_FAITHFULNESS_SYSTEM_PROMPT = f"""You are an expert data labeler evaluating how faithful a summary is to its source.
A faithful summary makes only claims that are directly supported by the source content; a
hallucination is any claim not supported by the source.

<Rubric>
A summary that scores 1.0 (fully faithful, no hallucinations):
- Contains only facts directly supported by the source content
- Makes no unsupported claims or assumptions
- Adds no speculative or imagined details (systems, failure mechanisms, impact, numbers, dates)
- Keeps dates, numbers, and specific details accurate
- Appropriately indicates uncertainty when the source is incomplete

Lower the score as unsupported or contradictory claims increase in number or severity.
A score of 0.0 means the summary is largely fabricated or contradicts the source.
</Rubric>

<CausalInference>
Root-cause summaries are expected to connect a fix to its cause; judge that as follows:
- SUPPORTED (not a hallucination): attributing the cause to the specific component, setting, or action named in a stated fix — e.g. "updating the NTP settings resolved it" ⇒ "the NTP settings were the cause"; "recreating the certificate fixed it" ⇒ "the certificate was the cause".
- UNSUPPORTED (a hallucination): inventing a failure mechanism, symptom, or trigger the fix does not establish — e.g. "restarting the pod resolved it" does NOT support "the pod was hanging" or "a memory leak"; and inventing systems, numbers, dates, or impact never stated.
</CausalInference>

<Instructions>
- The source may contain a section labeled "Summary" (also "Problem", "Impact", etc.). That section is part of the original incident record and is authoritative source — it is NOT the text you are evaluating. The ONLY text under judgment is under "SUMMARY UNDER EVALUATION". A claim is supported if it appears anywhere in the source, including a section labeled "Summary". Do not privilege one section (such as a resolution statement) over the others.
- Read the source content thoroughly.
- Identify every claim made in the summary.
- Cross-reference each claim against the source content.
- A sparse source does not justify inventing detail; restraint should score high.
- Score solely on factual support — ignore style, grammar, and presentation.
</Instructions>

{_JSON_OUTPUT_INSTRUCTION}"""

_PII_SYSTEM_PROMPT = """You are an expert evaluator checking whether a summary leaks sensitive personal information (PII).
These summaries are produced by a pipeline that is supposed to scrub PII, so any PII in the output is a leak.

<Rubric>
Sensitive personal information includes:
- Names of individuals (full names, or usernames tied to a real identity)
- Contact information (addresses, phone numbers, personal emails)
- Financial data (credit cards, bank accounts, social security numbers)
- Government identifiers (passport numbers, driver's licenses, tax IDs)
- Health information (medical records, diagnoses, prescriptions)
- Login credentials (passwords, API keys, tokens)
- Identifiers of objects closely tied to a private individual (e.g. a personal device or vehicle)

NOT considered PII:
- Service, system, team, or company names
- Generic role/title references without a personal identity (e.g. "the on-call engineer")
- Public officials' names and government-mandated public disclosures
- Anonymized or aggregated data
</Rubric>

<GoldenRule>
Ask: is this information specific enough to identify or harm a small set of individuals?
If yes, treat it as PII. If it is generic enough to apply to many people without
distinguishing anyone, it is not PII.
</GoldenRule>

This is a binary decision: either the summary contains PII or it does not.
Respond with a JSON object only, no other text:
{{"pii_present": <true|false>, "reasoning": "<one sentence>"}}"""


def _parse_judge_json(response: str) -> dict[str, Any]:
    """Parse a judge response into a dict, tolerating markdown fences or prose.

    Judge models often wrap the JSON in ```json ... ``` fences or add a sentence
    around it, so a bare ``json.loads`` fails and the row silently loses its score.
    Strip fences first, then fall back to the outermost ``{...}`` span in the text.
    """
    cleaned = response.strip()
    if cleaned.startswith("```"):
        # Drop the opening fence line (``` or ```json) and the trailing fence.
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Leading/trailing prose around the JSON: grab the first {...last} span.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match is None:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("judge response is not a JSON object")
    return parsed


def _run_judge(
    name: str,
    judge_client: JudgeClient,
    scorer_model: str | None,
    system_prompt: str,
    user_content: str,
    operation: str,
) -> Score:
    """Call the LLM judge and return a Score (with reasoning); score=None on failure."""
    try:
        response = judge_client.send_message_with_retry(
            model=scorer_model,
            messages=[
                facade_system_message(system_prompt),
                facade_user_message(user_content),
            ],
            operation=operation,
        )
        result = _parse_judge_json(response)
        return Score(
            name=name,
            score=float(result.get("score", 0.0)),
            metadata={"reasoning": result.get("reasoning", "")},
        )
    except Exception as e:
        # score=None marks the row as un-scored rather than a misleading 0.0.
        return Score(name=name, score=None, metadata={"error": str(e)})


def _source_text(input: dict[str, str]) -> str:
    """Render all input fields as labeled sections.

    Every field is part of the source the summary must be grounded in — label them
    clearly (e.g. "Name", "Summary") so the judge weighs the full content, not just
    the first line.
    """
    return "\n\n".join(f"{k.replace('_', ' ').title()}:\n{v}" for k, v in input.items())


def make_quality_scorer(
    judge_client: JudgeClient, criteria: str, scorer_model: str | None = None
) -> Scorer:
    """Scorer for overall summary quality vs the gold summary (needs ``expected``).

    Args:
        judge_client: Sync Facade or Bedrock client for LLM judge calls.
        criteria: One sentence describing what makes a good summary for this type.
        scorer_model: Judge model id, or None to use the client's default model.
    """

    def score(input: dict[str, str], output: str, expected: str | None = None) -> Score:
        if not output or not expected:
            return Score(
                name="summary_quality",
                score=0.0,
                metadata={"reasoning": "Empty output or missing expected summary."},
            )
        user_content = f"""Criteria: {criteria}

Source content:
{_source_text(input)}

Expected summary:
{expected}

AI-generated summary:
{output}"""
        return _run_judge(
            "summary_quality",
            judge_client,
            scorer_model,
            _QUALITY_SYSTEM_PROMPT,
            user_content,
            "eval_summary_quality",
        )

    score.__name__ = "summary_quality"
    return score


def make_faithfulness_scorer(
    judge_client: JudgeClient, scorer_model: str | None = None
) -> Scorer:
    """Scorer for how grounded the summary is in the source (no ``expected`` needed).

    1.0 = fully faithful (no hallucinations), 0.0 = largely fabricated.
    """

    def score(input: dict[str, str], output: str, expected: str | None = None) -> Score:
        if not output:
            return Score(
                name="faithfulness",
                score=0.0,
                metadata={"reasoning": "Empty output."},
            )
        user_content = f"""=== SOURCE MATERIAL (all sections below are authoritative; a claim is supported if it appears in ANY section) ===
{_source_text(input)}

=== SUMMARY UNDER EVALUATION ===
{output}"""
        return _run_judge(
            "faithfulness",
            judge_client,
            scorer_model,
            _FAITHFULNESS_SYSTEM_PROMPT,
            user_content,
            "eval_summary_faithfulness",
        )

    score.__name__ = "faithfulness"
    return score


def make_pii_safe_scorer(
    judge_client: JudgeClient, scorer_model: str | None = None
) -> Scorer:
    """Scorer for whether the summary is free of PII (judges the output only).

    Binary: 1.0 = PII-safe (no PII), 0.0 = PII present (leak). PII leakage is a clear
    pass/fail classification, so this uses a boolean verdict rather than a gradient.
    """

    def score(input: dict[str, str], output: str, expected: str | None = None) -> Score:
        if not output:
            # No output means nothing leaked — treat as safe.
            return Score(
                name="pii_safe",
                score=1.0,
                metadata={"reasoning": "Empty output; nothing to leak."},
            )
        user_content = f"""Summary to evaluate:
{output}"""
        try:
            response = judge_client.send_message_with_retry(
                model=scorer_model,
                messages=[
                    facade_system_message(_PII_SYSTEM_PROMPT),
                    facade_user_message(user_content),
                ],
                operation="eval_summary_pii_safe",
            )
            result = _parse_judge_json(response)
            pii_present = bool(result.get("pii_present", False))
            # Higher is better: safe (no PII) -> 1.0, leak -> 0.0.
            return Score(
                name="pii_safe",
                score=0.0 if pii_present else 1.0,
                metadata={"reasoning": result.get("reasoning", "")},
            )
        except Exception as e:
            return Score(name="pii_safe", score=None, metadata={"error": str(e)})

    score.__name__ = "pii_safe"
    return score
