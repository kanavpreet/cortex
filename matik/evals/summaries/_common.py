"""Shared harness for all summary eval modules.

Each summary type (incidentio_description, incidentio_root_cause,
github_pr_summary, jira_issue, jira_comments) has a thin ``eval.py`` that calls
``run_eval`` with its type-specific constants. The summary-specific wiring —
reading the production prompt, building the user message exactly like the
enricher, calling Facade, and choosing the scorers — lives here. The
category-neutral plumbing (kube-gen access, judge client, experiment naming,
tags/metadata) is shared via :mod:`evals._common`.
"""

import argparse
import textwrap
from collections.abc import Callable
from typing import Any, TypedDict

from braintrust import Eval, current_span, init_dataset
from genai_studio.braintrust import init_sdk

from common.clients.facade_client import (
    FacadeClientSync,
    create_facade_client_sync,
    facade_system_message,
    facade_user_message,
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
from evals.summaries.scorer import (
    make_faithfulness_scorer,
    make_pii_safe_scorer,
    make_quality_scorer,
)

# Eval category — tagged on every experiment so summary vs correlation runs can be
# filtered in Braintrust. Singular (the eval's type), distinct from the plural
# directory name.
CATEGORY = "summary"


# --- Golden-row schema (shared by every summary eval type) -------------------
# The shape of one row in a summary golden_dataset.yml. Kept here so all summary
# types reuse one model. `input` is the flat field map the enricher sends,
# `expected` is the gold summary text, `metadata` varies by type (always has a
# reference_id, used as the upsert id).


class GoldenRow(TypedDict):
    input: dict[str, str]
    expected: str
    metadata: dict[str, Any]


def build_user_content(input: dict[str, str], input_keys: list[str]) -> str:
    """Assemble the LLM user message exactly like the production enricher.

    Mirrors ``enricher/handlers/enrichment_handler.py`` ``_call_facade`` so evals
    test the same input the enricher sends. Each key becomes a labeled section
    ("issue_description" -> "Issue Description:\\n<value>"), joined by blank lines.
    """
    parts = []
    for key in input_keys:
        value = input.get(key) or ""
        label = key.replace("_", " ").title()
        parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def get_prompt_from_kube_gen(prompt_key: str) -> str:
    """Assemble the enricher's full system prompt from kube-gen.yml (deployed source of truth).

    The enricher never sends the source-mapping prompt alone: it substitutes it into
    the ``general_prompt`` template's ``{source_instructions}`` placeholder (mirrors
    ``EnricherConfig.build_prompt``). The eval must do the same so it tests the exact
    system prompt production ships — including the ``general_prompt``'s PII-scrubbing
    directive. Uses production-first, common.all-fallback resolution.
    """
    # YAML block scalars preserve leading indentation — strip it.
    source = textwrap.dedent(
        resolve_param("enricher", "source_mappings", prompt_key)
    ).strip()
    general = textwrap.dedent(resolve_param("enricher", "general_prompt")).strip()
    return general.format(source_instructions=source)


def make_task(
    facade_client: FacadeClientSync,
    system_prompt: str,
    input_keys: list[str],
    operation: str,
) -> Callable[[dict[str, str]], str]:
    """Build the Braintrust task: call Facade and log token usage per row."""

    def task(input: dict[str, str]) -> str:
        user_content = build_user_content(input, input_keys)
        text, prompt_tokens, completion_tokens = facade_client.send_message_with_usage(
            model=None,
            messages=[
                facade_system_message(system_prompt),
                facade_user_message(user_content),
            ],
            operation=operation,
        )
        current_span().log(
            metrics={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens": prompt_tokens + completion_tokens,
            }
        )
        return text

    return task


def run_eval(
    *,
    eval_slug: str,
    dataset_name: str,
    prompt_key: str,
    input_keys: list[str],
    operation: str,
    criteria: str,
) -> None:
    """Run one summary eval as a new Braintrust experiment.

    Args:
        eval_slug: Short kebab-case name for default experiment naming + tagging
            (e.g. "incidentio-root-cause").
        dataset_name: Braintrust golden dataset name to evaluate against.
        prompt_key: Key under source_mappings in kube-gen.yml for the system prompt.
        input_keys: Content keys to assemble into the user message (must match the
            enricher's input_keys for this type, and the seed dataset's input keys).
        operation: Facade operation label — equals the enricher's output_field
            (e.g. "root_cause_summary").
        criteria: One-sentence rubric passed to the LLM-judge scorer.
    """
    parser = argparse.ArgumentParser()
    add_judge_args(parser, eval_slug)
    args = parser.parse_args()

    experiment_name = make_experiment_name(CATEGORY, eval_slug, args.experiment)
    git_branch = current_git_branch()

    init_sdk()

    system_prompt = get_prompt_from_kube_gen(prompt_key)
    facade_config = load_facade_config()

    facade_client = create_facade_client_sync(
        facade_config=facade_config,
        common_config=None,
    )

    judge_client, scorer_model = build_judge_client(
        args.judge_provider, args.judge_model
    )
    scorers = [
        make_quality_scorer(judge_client, criteria, scorer_model),
        make_faithfulness_scorer(judge_client, scorer_model),
        make_pii_safe_scorer(judge_client, scorer_model),
    ]

    # Effective judge model: Facade with no explicit model uses the Facade default.
    effective_scorer_model = scorer_model or facade_config.default_model

    dataset = init_dataset(project=PROJECT, name=dataset_name)

    Eval(
        name=PROJECT,
        experiment_name=experiment_name,
        description=args.description,
        data=dataset,
        task=make_task(facade_client, system_prompt, input_keys, operation),
        scores=scorers,
        tags=build_tags(
            category=CATEGORY,
            eval_slug=eval_slug,
            git_branch=git_branch,
            generator_model=facade_config.default_model,
            scorer_model=effective_scorer_model,
        ),
        metadata=build_metadata(
            category=CATEGORY,
            generator_model=facade_config.default_model,
            judge_provider=args.judge_provider,
            scorer_model=effective_scorer_model,
            extra={"prompt": system_prompt},
        ),
    )
