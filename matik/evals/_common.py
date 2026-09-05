"""Shared, category-neutral plumbing for all Matik evals.

Both the summary and correlation harnesses build on this: reading the deployed
config from kube-gen.yml, building the LLM-judge client, naming experiments, and
the standard Braintrust tags/metadata. Category-specific logic (how the user
message is built, the task, the scorers) lives in each category's own ``_common``.
"""

import argparse
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from braintrust import init_dataset
from genai_studio.braintrust import init_sdk

from common.clients.bedrock_client import (
    BedrockClientSync,
    create_bedrock_client_sync,
)
from common.clients.facade_client import (
    FacadeClientSync,
    create_facade_client_sync,
)
from common.models.bedrock_config import BedrockConfig
from common.models.facade_config import FacadeConfig

PROJECT = "BizTech: Matik [Team: 791]"

# Default Facade/Bedrock endpoints for local runs (IAP-gated; require IAP_TOKEN).
# Override with FACADE_BASE_URL / BEDROCK_BASE_URL to point at a different endpoint.
_DEFAULT_FACADE_BASE_URL = "https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai"
_DEFAULT_BEDROCK_BASE_URL = "https://llm-fusion-hub.a.musta.ch/api/v2/proxy/aws/bedrock"

# The LLM judge runs on a different model family than the generator (Facade GPT)
# to avoid self-bias. Default: Claude Opus 4.8 via Bedrock; `global` cross-region
# routing is ~10% cheaper than `us`. Override with --judge-provider / --judge-model.
JUDGE_PROVIDERS = ("bedrock", "facade")
DEFAULT_JUDGE_PROVIDER = "bedrock"

# Common Bedrock model IDs for the judge, using `global` cross-region inference
# (~10% cheaper than region-specific). Full catalog:
# https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html
BEDROCK_JUDGE_MODELS = {
    "claude-opus-4-8": "global.anthropic.claude-opus-4-8",
    "claude-opus-4-7": "global.anthropic.claude-opus-4-7",
    "claude-opus-4-5": "global.anthropic.claude-opus-4-5",
    "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
    "claude-sonnet-4-5": "global.anthropic.claude-sonnet-4-5",
    "claude-haiku-4-5": "global.anthropic.claude-haiku-4-5",
    "llama-3-3-70b": "meta.llama3-3-70b-instruct-v1:0",
    "llama-3-1-405b": "meta.llama3-1-405b-instruct-v1:0",
}
_DEFAULT_JUDGE_MODELS = {
    "bedrock": BEDROCK_JUDGE_MODELS["claude-opus-4-8"],
}

# This file lives at matik/evals/_common.py — three parents up is the repo root.
_KUBE_GEN_PATH = Path(__file__).parent.parent.parent / "_infra/kube/kube-gen.yml"


def load_kube_gen() -> dict[str, Any]:
    with open(_KUBE_GEN_PATH) as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


def resolve_param(*path: str) -> Any:
    """Resolve a params path from kube-gen.yml, production-first then common.all.

    Evals validate what real users get, so prefer the production override
    (common.production.params.<path>) and fall back to the shared base config
    (common.all.params.<path>) when production doesn't set its own value.
    Raises KeyError if neither defines the full path.
    """
    common = load_kube_gen()["common"]

    for env in ("production", "all"):
        node = common.get(env, {}).get("params", {})
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if node is not None:
            return node

    raise KeyError(f"params path not found in kube-gen.yml: {'.'.join(path)}")


def load_facade_config() -> FacadeConfig:
    """Build facade config: model + resource bucket from kube-gen.yml, endpoint from env.

    The model and resource_bucket mirror production (read from kube-gen.yml) so
    evals test what real users get. The endpoint defaults to the IAP-gated public
    URL (requires IAP_TOKEN env var) and can be overridden with FACADE_BASE_URL.
    """
    return FacadeConfig(
        base_url=os.environ.get("FACADE_BASE_URL", _DEFAULT_FACADE_BASE_URL),
        default_model=resolve_param("facade", "default_model"),
        resource_bucket=resolve_param("facade", "resource_bucket"),
        mock_mode=os.environ.get("FACADE_MOCK_MODE", "false").lower() == "true",
    )


def build_judge_client(
    provider: str, model: str | None
) -> tuple[FacadeClientSync | BedrockClientSync, str | None]:
    """Build the LLM-judge client for the given provider.

    Returns (client, scorer_model). For Bedrock the model is passed per-call as
    the scorer_model; for Facade, scorer_model=None uses the Facade default.

    Args:
        provider: "bedrock" (default, Claude Opus 4.8) or "facade" (GPT).
        model: Explicit judge model id, or None to use the provider default.
    """
    if provider == "bedrock":
        # Accept either a friendly alias (e.g. "claude-sonnet-4-6") or a full
        # Bedrock model id.
        judge_model = model or _DEFAULT_JUDGE_MODELS["bedrock"]
        judge_model = BEDROCK_JUDGE_MODELS.get(judge_model, judge_model)
        config = BedrockConfig(
            base_url=os.environ.get("BEDROCK_BASE_URL", _DEFAULT_BEDROCK_BASE_URL),
            default_model=judge_model,
        )
        return create_bedrock_client_sync(bedrock_config=config), judge_model
    if provider == "facade":
        # model=None lets the scorer fall back to the Facade default model.
        return create_facade_client_sync(facade_config=load_facade_config()), model
    raise ValueError(
        f"Unknown judge provider {provider!r}; expected one of {JUDGE_PROVIDERS}"
    )


def add_judge_args(
    parser: argparse.ArgumentParser,
    eval_slug: str,
    default_provider: str = DEFAULT_JUDGE_PROVIDER,
) -> None:
    """Add the experiment + judge-selection args shared by every eval CLI.

    ``default_provider`` should be a different model family than the generator
    being evaluated, to avoid self-bias (summaries generate on Facade/GPT so they
    default the judge to Bedrock/Claude; correlations generate on Bedrock/Claude so
    they default the judge to Facade/GPT).
    """
    parser.add_argument(
        "--experiment",
        default=None,
        help=(
            "Optional suffix for the experiment name. The name always starts "
            f"'{eval_slug}-<timestamp>'; this suffix is appended, e.g. "
            f"'{eval_slug}-<timestamp>-<suffix>'."
        ),
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Optional description for this experiment run",
    )
    parser.add_argument(
        "--judge-provider",
        default=default_provider,
        choices=JUDGE_PROVIDERS,
        help=f"LLM judge provider (default: {default_provider})",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help=(
            "LLM judge model: a full model id, or a Bedrock alias "
            f"({', '.join(BEDROCK_JUDGE_MODELS)}). "
            "Defaults to the provider's default judge model."
        ),
    )


def make_experiment_name(category: str, eval_slug: str, suffix: str | None) -> str:
    """Experiment name: '<category>-<eval_slug>-<timestamp>', with an optional suffix.

    The category prefix groups runs (summaries vs correlations) in Braintrust; the
    slug + timestamp keep runs self-identifying; --experiment only appends a label
    describing what changed.
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"{category}-{eval_slug}-{timestamp}"
    return f"{name}-{suffix}" if suffix else name


def current_git_branch() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def build_tags(
    *,
    category: str,
    eval_slug: str,
    git_branch: str,
    generator_model: str,
    scorer_model: str,
) -> list[str]:
    """Standard experiment tags. All ``key:value`` so they filter cleanly."""
    return [
        f"category:{category}",
        f"eval_slug:{eval_slug}",
        f"git_branch:{git_branch}",
        f"model:{generator_model}",
        f"scorer:{scorer_model}",
    ]


def build_metadata(
    *,
    category: str,
    generator_model: str,
    judge_provider: str,
    scorer_model: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standard experiment metadata, plus any category-specific ``extra`` fields."""
    metadata: dict[str, Any] = {
        "category": category,
        "model": generator_model,
        "judge_provider": judge_provider,
        "scorer_model": scorer_model,
    }
    if extra:
        metadata.update(extra)
    return metadata


def load_golden_rows(module_file: str) -> list[dict[str, Any]]:
    """Load the golden rows from ``golden_dataset.yml`` next to a seed module.

    Data lives in YAML (separate from code) so it reads and reviews as plain data.
    Pass ``__file__`` from the seed module.
    """
    path = Path(module_file).parent / "golden_dataset.yml"
    with open(path) as f:
        rows: list[dict[str, Any]] = yaml.safe_load(f)
    return rows


def seed_golden_dataset(
    *,
    dataset_name: str,
    description: str,
    category: str,
    rows: list[Any],
) -> None:
    """Upsert golden ``rows`` into a Braintrust dataset (shared by all seed modules).

    Each row must have ``metadata.reference_id`` (used as the upsert id), ``input``,
    and ``expected``. Re-seeding is safe — only changed rows update.
    """
    init_sdk()
    dataset = init_dataset(
        project=PROJECT,
        name=dataset_name,
        description=description,
        metadata={"category": category},
    )
    for row in rows:
        dataset.insert(
            id=row["metadata"]["reference_id"],
            input=row["input"],
            expected=row["expected"],
            metadata=row["metadata"],
        )
    dataset.flush()
    print(f"Seeded {len(rows)} rows into '{dataset_name}' in project '{PROJECT}'")
