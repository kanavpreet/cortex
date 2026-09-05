"""Correlation classification for the root-cause-coverage audit.

Root Cause is decided deterministically — an exact ``(entity_type, entity_id)``
match against the verified gold entity, mirroring
``evals/correlations/scorer.py``'s set-comparison approach. Everything else is
Relevant or Noise, also decided deterministically — by service overlap with
the incident's own root-cause/affected services (see ``classify_by_service_match``)
— never an LLM judgment call: a candidate whose service can't be confirmed to
overlap is Noise, not "probably fine."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from common.clients.bedrock_client import BedrockClientSync
from common.clients.facade_client import FacadeClientSync
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.root_cause_audit import VerifiedEntity
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

LLMClient = FacadeClientSync | BedrockClientSync
RelevantOrNoise = Literal["relevant", "noise"]


@dataclass(frozen=True)
class ClassificationResult:
    """Deterministic classification: exact matches vs. everything else.

    ``root_cause_matches`` should in practice hold 0 or 1 entries — the gold
    entity is a single identifier, so at most one correlation row can match it
    exactly. ``unresolved`` is what ``classify_by_service_match`` consumes
    next; this module makes no claim about those beyond "not the root cause".
    """

    root_cause_matches: list[ReliabilityCorrelation] = field(default_factory=list)
    unresolved: list[ReliabilityCorrelation] = field(default_factory=list)

    @property
    def root_cause_covered(self) -> bool:
        """Whether Matik surfaced the actual root cause — Root Cause Coverage."""
        return len(self.root_cause_matches) > 0


def is_root_cause_match(
    correlation: ReliabilityCorrelation, gold_entity: VerifiedEntity
) -> bool:
    """Exact ``(entity_type, entity_id)`` match — the only deterministic check."""
    return (
        correlation.entity_type == gold_entity.entity_type
        and correlation.entity_id == gold_entity.entity_id
    )


def classify_root_cause_matches(
    correlations: list[ReliabilityCorrelation], gold_entity: VerifiedEntity | None
) -> ClassificationResult:
    """Split correlations into exact root-cause matches and everything else.

    If there's no verified gold entity (extraction abstained or verification
    failed), nothing can be a root-cause match — every correlation is unresolved.
    """
    if gold_entity is None:
        return ClassificationResult(
            root_cause_matches=[], unresolved=list(correlations)
        )

    matches = [c for c in correlations if is_root_cause_match(c, gold_entity)]
    unresolved = [c for c in correlations if not is_root_cause_match(c, gold_entity)]
    return ClassificationResult(root_cause_matches=matches, unresolved=unresolved)


def _strip_markdown_json_fence(text: str) -> str:
    """Strip a ```json ... ``` (or bare ``` ... ```) code fence some models
    wrap their JSON response in — otherwise json.loads chokes on the leading
    backtick and a real response gets treated as a parse failure."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _normalize_service_name(name: str) -> str:
    """Punctuation/case-insensitive form for substring comparison: treat
    delimiter characters as equivalent rather than requiring an exact string
    match, matching how the correlation engine itself normalizes service
    names."""
    return re.sub(r"[-_.\s]+", "", name.lower())


def _services_overlap(
    correlation_services: list[str] | None, incident_services: set[str]
) -> bool:
    if not correlation_services or not incident_services:
        return False
    normalized_incident = {_normalize_service_name(s) for s in incident_services if s}
    for service in correlation_services:
        normalized = _normalize_service_name(service)
        if any(
            normalized in other or other in normalized for other in normalized_incident
        ):
            return True
    return False


def classify_by_service_match(
    correlations: list[ReliabilityCorrelation],
    incident_services: set[str],
) -> list[tuple[ReliabilityCorrelation, RelevantOrNoise]]:
    """The only Relevant-vs-Noise classifier: deterministic service overlap
    against the incident's own (root-cause or affected) services. Used both
    for non-root-cause correlations once a gold entity is known, and for
    every correlation when a human has confirmed there's no PR/TCMR root
    cause to compare against at all. Never an LLM call, and never
    ``root_cause`` -- that's decided separately by ``classify_root_cause_matches``.
    """
    return [
        (
            correlation,
            "relevant"
            if _services_overlap(correlation.services, incident_services)
            else "noise",
        )
        for correlation in correlations
    ]


def build_correlation_rows(
    reference_id: str,
    time_bucket: Literal["investigation", "postmortem"],
    root_cause_matches: list[ReliabilityCorrelation],
    relevance_pairs: list[tuple[ReliabilityCorrelation, RelevantOrNoise]],
) -> list[dict[str, str]]:
    """Flatten a fully-classified correlation set into one dict per correlation.

    One row per correlation (rather than one JSON blob per incident) keeps the
    Correlations sheet pivot/dashboard-friendly — countable by classification,
    entity_type, or time_bucket without first parsing a cell's contents.
    """
    source = "braintrust" if time_bucket == "investigation" else "database"
    rows: list[dict[str, str]] = [
        {
            "reference_id": reference_id,
            "time_bucket": time_bucket,
            "source": source,
            "entity_type": c.entity_type,
            "entity_id": c.entity_id,
            "services": ", ".join(c.services) if c.services else "",
            "classification": "root_cause",
            "reasoning": c.reasoning or "",
            "base_llm_score": "" if c.base_score is None else str(c.base_score),
        }
        for c in root_cause_matches
    ]
    rows.extend(
        {
            "reference_id": reference_id,
            "time_bucket": time_bucket,
            "source": source,
            "entity_type": c.entity_type,
            "entity_id": c.entity_id,
            "services": ", ".join(c.services) if c.services else "",
            "classification": classification,
            "reasoning": c.reasoning or "",
            "base_llm_score": "" if c.base_score is None else str(c.base_score),
        }
        for c, classification in relevance_pairs
    )
    return rows
