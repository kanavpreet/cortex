"""Shared harness for all correlation eval modules.

Each correlation type (currently: incident) has a thin ``eval.py`` that calls
``run_eval`` with its constants. The eval calls the LLM with the live
``correlation_system_prompt`` and parses it with the shared production parser,
using the SAME provider/model the correlator runs in production (``llm_provider``
in kube-gen — currently Bedrock/Claude) — only the data fetching is replaced by the
golden dataset.

Correctness is a retrieval problem (which candidate IDs are causal), so the scorers
are deterministic precision/recall/F1 plus structure, calibration, and one LLM-judge
for reasoning quality. The judge defaults to a different model family than the
generator (Facade/GPT, since the correlator generates on Bedrock/Claude) to avoid
self-bias. Category-neutral plumbing is shared via :mod:`evals._common`.
"""

import argparse
import asyncio
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypedDict

import yaml
from braintrust import Eval, current_span, init_dataset
from genai_studio.braintrust import init_sdk

from common.clients.bedrock_client import create_bedrock_client
from common.clients.facade_client import (
    create_facade_client,
    facade_system_message,
    facade_user_message,
)
from common.models.bedrock_config import BedrockConfig
from common.models.enigmatologist_config import EnigmatologistConfig
from enigmatologist.correlations.reliability.incident.nodes import (
    _build_user_prompt,
    parse_llm_correlations,
)
from enigmatologist.correlations.reliability.incident.state import (
    ChangeEvent,
    IncidentCorrelationState,
)
from evals._common import (
    PROJECT,
    add_judge_args,
    build_judge_client,
    build_metadata,
    build_tags,
    current_git_branch,
    load_facade_config,
    make_experiment_name,
    resolve_param,
)
from evals.correlations.scorer import (
    make_calibration_scorer,
    make_f1_scorer,
    make_precision_scorer,
    make_reasoning_scorer,
    make_recall_scorer,
    make_structure_scorer,
)

CATEGORY = "correlation"


# --- Golden-row schema (shared by every correlation eval type) ---------------
# The shape of one row in a correlation golden_dataset.yml. Kept here so all
# correlation types (incident, and future anchors) reuse one model.


class GoldenChangeEvent(TypedDict):
    id: str
    description: str
    timestamp: str
    services: list[str]


class GoldenInput(TypedDict):
    incident_description: str
    incident_created_at: str
    incident_affected_services: list[str]
    github_events: list[GoldenChangeEvent]
    jira_events: list[GoldenChangeEvent]


class GoldenExpected(TypedDict):
    correlated_ids: list[str]
    # Expected confidence band [lo, hi] per correlated id, for score calibration.
    score_bands: dict[str, list[float]]


class GoldenRowMetadata(TypedDict):
    reference_id: str
    scenario: str


class GoldenRow(TypedDict):
    input: GoldenInput
    expected: GoldenExpected
    metadata: GoldenRowMetadata


def _coerce_patterns(value: Any) -> list[str]:
    """kube-gen stores speculative_patterns as a stringified list; parse it.

    Uses yaml (not ast.literal_eval) so single-quoted regex escapes like ``\\b``
    stay literal rather than being interpreted as control characters.
    """
    if isinstance(value, str):
        parsed = yaml.safe_load(value)
        return list(parsed) if parsed else []
    return list(value)


def load_enigmatologist_config() -> EnigmatologistConfig:
    """Build a minimal EnigmatologistConfig from kube-gen.yml for the LLM node.

    Only the fields the LLM correlation node reads — the prompt and the
    post-LLM filtering knobs — so the eval matches production behavior.
    """
    return EnigmatologistConfig(
        correlation_system_prompt=resolve_param(
            "enigmatologist", "correlation_system_prompt"
        ),
        min_llm_score=resolve_param("enigmatologist", "min_llm_score"),
        speculative_patterns=_coerce_patterns(
            resolve_param("enigmatologist", "speculative_patterns")
        ),
        speculative_max_score=resolve_param("enigmatologist", "speculative_max_score"),
    )


def _change_events(items: list[dict[str, Any]]) -> list[ChangeEvent]:
    return [
        ChangeEvent(
            id=e["id"],
            description=e.get("description", ""),
            start_time=datetime.fromisoformat(e["timestamp"]),
            services=e.get("services"),
        )
        for e in items
    ]


def build_state(input: dict[str, Any]) -> IncidentCorrelationState:
    """Assemble the graph state the LLM node reads from a dataset input row."""
    return {
        "incident_id": input.get("incident_id", ""),
        "incident_description": input.get("incident_description", ""),
        "incident_created_at": datetime.fromisoformat(input["incident_created_at"]),
        "incident_affected_services": input.get("incident_affected_services", []),
        "jira_time_field": "",
        "github_time_field": "",
        "jira_events": _change_events(input.get("jira_events", [])),
        "github_events": _change_events(input.get("github_events", [])),
        "service_correlation_result": None,
        "llm_correlation_result": None,
        "correlation_group": None,
    }


def load_generator() -> tuple[Callable[[], Any], str, str]:
    """Build the correlator's generator exactly as production runs it.

    Reads ``enigmatologist.llm_provider`` from kube-gen and returns a
    (client_factory, model, provider) tuple. The factory makes a fresh async
    client per row so each call stays bound to its own event loop. Currently
    production is Bedrock/Claude (``bedrock.default_model``); Facade is supported
    for completeness.
    """
    provider = resolve_param("enigmatologist", "llm_provider")
    if provider == "bedrock":
        model = resolve_param("bedrock", "default_model")
        # Default base_url is the public IAP URL (from BedrockConfig); override for CI.
        base_url = os.environ.get("BEDROCK_BASE_URL")

        def bedrock_factory() -> Any:
            config = (
                BedrockConfig(default_model=model, base_url=base_url)
                if base_url
                else BedrockConfig(default_model=model)
            )
            return create_bedrock_client(bedrock_config=config)

        return bedrock_factory, model, provider

    facade_config = load_facade_config()

    def facade_factory() -> Any:
        return create_facade_client(facade_config=facade_config)

    return facade_factory, facade_config.default_model, provider


def make_task(
    client_factory: Callable[[], Any], eg_config: EnigmatologistConfig
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the Braintrust task: call the LLM with the production correlation
    prompt, then parse it with the shared production parser.

    Calls the LLM directly (rather than the graph node) so the raw response is
    available for the ``output_structure`` scorer, but builds the user prompt with
    the node's own ``_build_user_prompt`` and parses with ``parse_llm_correlations``
    so the prompt + parse/filter exactly match production.

    Returns ``{"selected": [{"id", "score", "reasoning"}, ...], "raw": <str>}``.
    """

    def task(input: dict[str, Any]) -> dict[str, Any]:
        async def _call_llm() -> tuple[str, int, int]:
            # Fresh async client per row keeps each call bound to its own loop.
            client = client_factory()
            state = build_state(input)
            messages = [
                facade_system_message(eg_config.correlation_system_prompt),
                facade_user_message(_build_user_prompt(state)),
            ]
            result: tuple[str, int, int] = await client.send_message_with_usage(
                model=None, messages=messages, operation="incident_correlation"
            )
            return result

        raw, prompt_tokens, completion_tokens = asyncio.run(_call_llm())
        current_span().log(
            metrics={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens": prompt_tokens + completion_tokens,
            }
        )
        selected: list[dict[str, Any]] = []
        try:
            result = parse_llm_correlations(
                raw,
                eg_config.min_llm_score,
                eg_config.speculative_patterns,
                eg_config.speculative_max_score,
            )
            for m in result.biztech_github + result.jira:
                # Use base_score: the raw LLM confidence (post min-score filter).
                # The LLM_SCORE_CAP (final_score = base_score * 0.60) is downstream
                # business logic applied by score_llm_correlations, which we don't
                # run — the eval grades the prompt's raw score, not the capped one.
                selected.append(
                    {"id": m.id, "score": m.base_score, "reasoning": m.reasoning}
                )
        except Exception:
            # Malformed output -> no selection; the output_structure scorer flags it.
            selected = []
        return {"selected": selected, "raw": raw}

    return task


def run_eval(*, eval_slug: str, dataset_name: str) -> None:
    """Run one correlation eval as a new Braintrust experiment.

    Args:
        eval_slug: Short kebab-case name for experiment naming + tagging.
        dataset_name: Braintrust golden dataset name to evaluate against.
    """
    client_factory, generator_model, generator_provider = load_generator()
    eg_config = load_enigmatologist_config()

    # Judge defaults to a different family than the generator to avoid self-bias:
    # Bedrock/Claude generator -> Facade/GPT judge (and vice versa).
    default_judge = "facade" if generator_provider == "bedrock" else "bedrock"

    parser = argparse.ArgumentParser()
    add_judge_args(parser, eval_slug, default_provider=default_judge)
    args = parser.parse_args()

    experiment_name = make_experiment_name(CATEGORY, eval_slug, args.experiment)
    git_branch = current_git_branch()

    init_sdk()

    judge_client, scorer_model = build_judge_client(
        args.judge_provider, args.judge_model
    )
    scorers = [
        make_precision_scorer(),
        make_recall_scorer(),
        make_f1_scorer(),
        make_structure_scorer(),
        make_calibration_scorer(),
        make_reasoning_scorer(judge_client, scorer_model),
    ]

    # Facade judge with no explicit model uses the Facade default model.
    effective_scorer_model = scorer_model or load_facade_config().default_model

    dataset = init_dataset(project=PROJECT, name=dataset_name)

    Eval(
        name=PROJECT,
        experiment_name=experiment_name,
        description=args.description,
        data=dataset,
        task=make_task(client_factory, eg_config),
        scores=scorers,
        tags=build_tags(
            category=CATEGORY,
            eval_slug=eval_slug,
            git_branch=git_branch,
            generator_model=generator_model,
            scorer_model=effective_scorer_model,
        ),
        metadata=build_metadata(
            category=CATEGORY,
            generator_model=generator_model,
            judge_provider=args.judge_provider,
            scorer_model=effective_scorer_model,
            extra={"correlation_system_prompt": eg_config.correlation_system_prompt},
        ),
    )
