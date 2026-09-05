"""Ground-truth extraction and verification for the root-cause-coverage audit.

Extraction is deliberately extractive, not generative: the model must quote an
identifier verbatim from the source text or abstain (return null) — that's the
hallucination guard. Verification is plain code, not another LLM call: any
cited identifier is checked against GHE/JIRA before being trusted as gold.

``source_traceable`` is the one judgment call extraction is allowed to make
rather than quote: whether the root cause has some traceable external source
even without a verified identifier (e.g. a vendor status page), reported as
``None`` when genuinely ambiguous so a human decides. It's superseded by a
verified root cause (see ``pipeline.py``), which is always traceable
regardless of what this returns.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from audit.root_cause_coverage.classification import (
    LLMClient,
    _strip_markdown_json_fence,
)
from audit.root_cause_coverage.sources import all_root_cause_sources
from common.clients.facade_client import facade_system_message, facade_user_message
from common.llm_tracing.operation import traced_llm_operation
from common.models.root_cause_audit import GroundTruth, VerifiedEntity
from common.utils import log_utils

if TYPE_CHECKING:
    from audit.root_cause_coverage.pipeline import RootCauseAuditContext

logger = log_utils.get_logger(__name__)


def build_source_text(
    incident_summary: str | None,
    update_messages: list[str],
    channel_summary: str | None = None,
) -> str:
    """Concatenate incident.io's summary, chronological update messages, and
    Matik's own Slack-channel summary into one extraction source text.

    incident.io's own summary carries the incident narrative but doesn't
    reliably contain a specific citation — that often only shows up in an
    update message. ``channel_summary`` is a third, independent source
    (Matik's own OpsBot-derived summary of the incident's Slack channel) —
    Slack discussion sometimes names the PR/TCMR even when incident.io's own
    fields don't.
    """
    parts = []
    if incident_summary:
        parts.append(f"Incident summary:\n{incident_summary}")
    if update_messages:
        joined = "\n\n".join(update_messages)
        parts.append(f"Incident updates (chronological):\n{joined}")
    if channel_summary:
        parts.append(f"Slack channel summary:\n{channel_summary}")
    return "\n\n".join(parts)


def extract_ground_truth(
    llm_client: LLMClient,
    source_text: str,
    incident_reference_id: str,
    system_prompt: str,
    entity_created_at: object = None,
    model: str | None = None,
) -> GroundTruth:
    """Run the extraction LLM call and parse its response.

    Malformed JSON or a failed call is treated the same as an explicit
    abstention — never crash the pipeline over one incident's LLM response,
    and never fabricate a citation to fill the gap.
    """
    with traced_llm_operation(
        operation="root_cause_audit_extraction",
        source="root_cause_audit",
        entity_id={"incident_id": incident_reference_id},
        entity_created_at=entity_created_at,  # type: ignore[arg-type]
    ):
        try:
            response = llm_client.send_message_with_retry(
                model=model,
                messages=[
                    facade_system_message(system_prompt),
                    facade_user_message(source_text),
                ],
                operation="root_cause_audit_extraction",
            )
            result = json.loads(_strip_markdown_json_fence(response))
        except Exception:
            logger.exception(
                "ground truth extraction failed, treating as abstention",
                incident_reference_id=incident_reference_id,
            )
            return GroundTruth(
                change_related=False,
                ownership="unknown",
                cited_identifier=None,
                quote_span=None,
                reasoning="extraction failed",
                source_traceable=None,
            )

    raw_source_traceable = result.get("source_traceable")
    return GroundTruth(
        change_related=bool(result.get("change_related", False)),
        ownership=result.get("ownership", "unknown"),
        cited_identifier=result.get("cited_identifier"),
        quote_span=result.get("quote_span"),
        reasoning=result.get("reasoning", ""),
        source_traceable=(
            raw_source_traceable if isinstance(raw_source_traceable, bool) else None
        ),
    )


def verify_ground_truth(
    ground_truth: GroundTruth,
    context: RootCauseAuditContext,
) -> VerifiedEntity | None:
    """Confirm a cited identifier actually exists before trusting it as gold.

    Tries every registered root-cause source in turn (see
    ``audit/root_cause_coverage/sources/``) — the first whose identifier
    pattern matches wins. Returns None if extraction abstained, no source's
    pattern matched, or the matching source's identifier doesn't resolve —
    any of which means: park this incident for human review, don't score it.
    """
    if not ground_truth.cited_identifier:
        return None

    for spec in all_root_cause_sources():
        parsed = spec.parse(ground_truth.cited_identifier, ground_truth.quote_span)
        if parsed is None:
            continue
        return spec.verify(context, parsed)

    logger.info(
        "cited identifier did not match any registered source",
        cited_identifier=ground_truth.cited_identifier,
    )
    return None
