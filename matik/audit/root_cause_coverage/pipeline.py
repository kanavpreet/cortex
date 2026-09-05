"""Ties ground truth, correlation retrieval, and classification together into
the three callables an ``AuditSpec`` needs.

Each incident becomes one Incidents-tab row, tracked by two independent
columns: ``audit_status`` (what got concluded: ``needs_human_review``,
``llm_reviewed``, ``human_reviewed``, or ``error``) and ``overall_status``
(the processing lifecycle: ``ongoing`` or ``final``, generic across audit
types — see ``audit.sheet_sync``). Correlations only ever get fetched and
written once a row reaches a conclusion — the LLM finds a root cause
directly (``llm_reviewed``), or a human resolves it later, either by
supplying a root cause that verifies or by confirming there isn't one
(``human_reviewed`` either way). A row a human hasn't touched yet never gets
its correlations fetched at all.

Extraction/verification and the correlation fetch are separate failure
domains: extraction is an LLM call and verification is a cheap GHE/Jira
check, while the correlation fetch depends on Braintrust and can fail for
infra/permission reasons (RBAC, network) unrelated to whether the incident's
root cause was actually found. So a correlation-fetch failure gets its own
``error`` status rather than falling back to ``needs_human_review`` — the
root cause (or its confirmed absence) is already known and shouldn't need
re-extracting; only the fetch needs retrying. ``error`` rows carry
``error_detail``/``error_pod`` (which pod's run produced the failure, for log
lookup) and whatever gold-entity info was already resolved, so the next run's
retry can resume from there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from opentelemetry import trace

from audit.root_cause_coverage.classification import (
    LLMClient,
    build_correlation_rows,
    classify_by_service_match,
    classify_root_cause_matches,
)
from audit.root_cause_coverage.ground_truth import (
    build_source_text,
    extract_ground_truth,
    verify_ground_truth,
)
from audit.root_cause_coverage.sources import get_root_cause_source
from audit.sheet_sync import FINAL, ONGOING
from common.models.incidentio_incident import IncidentIOIncident
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.root_cause_audit import GroundTruth, VerifiedEntity
from common.utils import log_utils
from common.utils.incidentio_utils import build_incident_from_payload

if TYPE_CHECKING:
    from common.clients.braintrust_client import BraintrustClient
    from common.clients.ghe_client import GHEClient
    from common.clients.incidentio_client import IncidentIOClientSync
    from common.clients.jira_client import JiraClient
    from common.daos.ghe_pr_dao import GHEPRDAO
    from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO
    from common.daos.reliability_correlation_dao import ReliabilityCorrelationDAO

_tracer = trace.get_tracer(__name__)


class CorrelationsSheetsClient(Protocol):
    """What this module needs to log correlations — narrower than the full
    ``SheetsClient`` so a dry-run stand-in doesn't need to fake read/upsert too."""

    def append_rows(self, rows: list[dict[str, Any]]) -> None: ...


@dataclass
class RootCauseAuditContext:
    """Everything ``find_eligible_entities``/``process_entity``/``rescore_entity``
    need."""

    incident_dao: IncidentIOIncidentDAO
    correlation_dao: ReliabilityCorrelationDAO
    ghe_pr_dao: GHEPRDAO
    incidentio_client: IncidentIOClientSync
    ghe_client: GHEClient
    jira_client: JiraClient
    llm_client: LLMClient
    braintrust_client: BraintrustClient
    correlations_sheets_client: CorrelationsSheetsClient
    ground_truth_extraction_prompt: str
    incident_type_ids: list[str]
    lookback_hours: int = 24
    investigation_window_minutes: int = 20


logger = log_utils.get_logger(__name__)

NEEDS_HUMAN_REVIEW = "needs_human_review"
LLM_REVIEWED = "llm_reviewed"
HUMAN_REVIEWED = "human_reviewed"
ERROR = "error"


def _current_pod_name() -> str:
    """The running pod's name, from the ``K8S_POD_NAME`` env var kube-gen sets
    on every workload. Stored alongside an error so a human can trace a
    failure back to the exact run that produced it."""
    return os.environ.get("K8S_POD_NAME", "unknown")


def _watchpoint_log_url(pod_name: str, failed_at: datetime) -> str:
    """A direct link to this pod's logs around the failure, so a reviewer can
    jump straight to them instead of hunting by timestamp. Absolute
    millisecond timestamps (not "now-30m"/"now") -- a relative window would
    point at whatever's recent *when the link is opened*, not the failure."""
    from_ms = int((failed_at - timedelta(minutes=15)).timestamp() * 1000)
    to_ms = int((failed_at + timedelta(minutes=15)).timestamp() * 1000)
    return (
        "https://grafana.a.musta.ch/a/watchpoint?wp=workspace[0]:logs/watchpoint-logs"
        f"?from={from_ms}&pod={pod_name}&to={to_ms}&var-project=matik"
    )


def find_eligible_entities(context: RootCauseAuditContext) -> list[dict[str, Any]]:
    """Newly-terminal, Biztech, public incidents from the last ``lookback_hours``,
    as sheet-row seeds -- fetched directly from incident.io, not the local DB.

    The DB's ``incidentio_incidents`` table is also populated by chronicler's
    webhook path, which (unlike historian's crawler) applies neither the
    incident-type nor the visibility filter, so it can't be trusted as a
    pre-filtered source here. Fetching straight from incident.io re-applies
    both filters (type via ``incident_type_ids``, visibility inside
    ``list_incidents`` itself) at the point this audit actually needs them.
    """
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=context.lookback_hours
    )
    incidents = context.incidentio_client.list_all_incidents(
        incident_type_ids=context.incident_type_ids,
        updated_at_gte=since.strftime("%Y-%m-%d"),
    )

    eligible: list[dict[str, Any]] = []
    for item in incidents:
        incident = item.incident
        if incident.status_category not in ("closed", "canceled"):
            continue
        settled_at = incident.closed_at or incident.canceled_at or incident.updated_at
        if settled_at is None or settled_at < since:
            continue
        eligible.append(
            {
                "reference_id": incident.reference_id,
                "incident_id": incident.incident_id,
                "created_at": incident.created_at,
            }
        )
    return eligible


def _build_incident_context(
    incident_data: dict[str, Any],
    incident: IncidentIOIncident,
    entity_created_at: datetime,
) -> dict[str, str]:
    """Reviewer-facing context pulled from the same ``get_incident`` payload
    extraction already fetches — no extra API calls needed. ``incident`` is
    that same payload already parsed into our row model (via
    ``build_incident_from_payload``), reused here for root_cause_service/
    affected_services rather than re-deriving them from the raw dict."""
    slack_channel_id = incident_data.get("slack_channel_id") or ""
    return {
        "incident_url": incident_data.get("permalink") or "",
        "slack_channel_url": (
            f"https://airbnb.slack.com/archives/{slack_channel_id}"
            if slack_channel_id
            else ""
        ),
        "description": incident_data.get("summary") or "",
        "incident_date": entity_created_at.isoformat(),
        "incident_status": (incident_data.get("incident_status") or {}).get("category")
        or "",
        "root_cause_service": incident.root_cause_service or "",
        "affected_services": (
            ", ".join(incident.affected_services) if incident.affected_services else ""
        ),
    }


def _enrich_correlation_links(
    rows: list[dict[str, str]], context: RootCauseAuditContext
) -> list[dict[str, str]]:
    """Add a ``url`` to each correlation row, via each entity type's own
    ``build_links`` (if it has one) -- batched per type so e.g. github_pr's
    DB lookup happens once per run, not once per row. A source with no
    ``build_links``, or an id it can't resolve, leaves the row's url blank."""
    by_type: dict[str, list[str]] = {}
    for row in rows:
        entity_type = row.get("entity_type", "")
        entity_id = row.get("entity_id", "")
        if entity_type and entity_id:
            by_type.setdefault(entity_type, []).append(entity_id)

    links: dict[tuple[str, str], str] = {}
    for entity_type, entity_ids in by_type.items():
        spec = get_root_cause_source(entity_type)
        if spec is None or spec.build_links is None:
            continue
        for entity_id, url in spec.build_links(entity_ids, context).items():
            links[(entity_type, entity_id)] = url

    for row in rows:
        row["url"] = links.get(
            (row.get("entity_type", ""), row.get("entity_id", "")), ""
        )
    return rows


def _fetch_correlations(
    context: RootCauseAuditContext, reference_id: str, entity_created_at: datetime
) -> dict[Literal["postmortem", "investigation"], list[ReliabilityCorrelation]]:
    """Both correlation buckets for one incident, unclassified."""
    with _tracer.start_as_current_span("fetch_correlations") as span:
        span.set_attribute("reference_id", reference_id)
        return {
            "postmortem": context.correlation_dao.find_by_anchor(reference_id),
            "investigation": context.braintrust_client.get_investigation_time_correlations(
                incident_id=reference_id,
                entity_created_at=entity_created_at,
                window_minutes=context.investigation_window_minutes,
            ),
        }


def _dedupe_across_buckets(
    rows_by_label: dict[Literal["postmortem", "investigation"], list[dict[str, str]]],
) -> list[dict[str, str]]:
    """Postmortem's correlation table is an upserted accumulation of every
    rerun ever done for the incident, so anything caught during investigation
    is always still sitting there at postmortem too -- re-listing the same
    entity under both buckets is guaranteed duplication, not new signal.
    Keeps the investigation-time row when an entity appears in both."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label in ("investigation", "postmortem"):
        for row in rows_by_label.get(label, []):
            key = (row.get("entity_type", ""), row.get("entity_id", ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def _classify_correlations(
    context: RootCauseAuditContext,
    reference_id: str,
    entity_created_at: datetime,
    gold_entity: VerifiedEntity | None,
    incident: IncidentIOIncident,
) -> tuple[list[dict[str, str]], dict[str, bool | str]]:
    """Classify both correlation buckets.

    Returns one row per correlation across both buckets (an entity caught in
    both is only listed once — see ``_dedupe_across_buckets``), ready to
    append to the Correlations sheet (never merged into the incident row
    itself), plus Root Cause Coverage per bucket — whether Matik's own
    correlation engine surfaced the real root cause, the audit's namesake
    metric — keyed by time bucket ("postmortem"/"investigation") so
    investigation-time and postmortem-time coverage stay distinguishable on
    the incident row. Coverage is computed from each bucket's full,
    unfiltered correlation set, independent of the row-level dedup.

    Relevant-vs-Noise is always decided by deterministic service overlap
    against the incident's own root-cause/affected services (see
    ``classify_by_service_match``) — never an LLM judgment call.
    ``gold_entity=None`` means a human confirmed no PR/TCMR root cause
    exists: nothing can be ``root_cause`` and coverage is "" (not
    applicable) for that bucket, but candidates still get classified
    relevant/noise the same way.
    """
    incident_services = {
        s
        for s in [incident.root_cause_service, *(incident.affected_services or [])]
        if s
    }
    buckets = _fetch_correlations(context, reference_id, entity_created_at)

    coverage: dict[str, bool | str] = {}
    rows_by_label: dict[
        Literal["postmortem", "investigation"], list[dict[str, str]]
    ] = {}
    for label, correlations in buckets.items():
        if gold_entity is not None:
            classified = classify_root_cause_matches(correlations, gold_entity)
            coverage[label] = classified.root_cause_covered
            unresolved = classified.unresolved
            root_cause_matches = classified.root_cause_matches
        else:
            coverage[label] = ""
            unresolved = correlations
            root_cause_matches = []
        relevance_pairs = classify_by_service_match(unresolved, incident_services)
        rows_by_label[label] = build_correlation_rows(
            reference_id, label, root_cause_matches, relevance_pairs
        )
    deduped = _dedupe_across_buckets(rows_by_label)
    return _enrich_correlation_links(deduped, context), coverage


def _run_classification(
    context: RootCauseAuditContext,
    reference_id: str,
    entity_created_at: datetime,
    gold_entity: VerifiedEntity | None,
    incident: IncidentIOIncident,
    success_status: str,
) -> dict[str, Any]:
    """Fetch and classify correlations, returning the fields to merge into a
    sheet row: either ``success_status``/``final`` with coverage, or ``error``
    with diagnostic detail if the fetch itself failed (e.g. a Braintrust RBAC
    error) -- the root cause (or its confirmed absence) is already known at
    this point, so a fetch failure shouldn't force re-extraction, only a
    retry of this step.

    ``gold_entity=None`` means a human confirmed no PR/TCMR root cause exists.
    """
    try:
        correlation_rows, coverage = _classify_correlations(
            context, reference_id, entity_created_at, gold_entity, incident
        )
        coverage_fields: dict[str, Any] = {
            "root_cause_covered_postmortem": coverage["postmortem"],
            "root_cause_covered_investigation": coverage["investigation"],
        }
    except Exception as exc:
        logger.exception(
            "correlation fetch/classification failed, will retry",
            reference_id=reference_id,
        )
        pod_name = _current_pod_name()
        return {
            "audit_status": ERROR,
            "overall_status": ONGOING,
            "error_detail": f"correlation fetch/classification failed: {exc}",
            "error_pod": pod_name,
            "error_log_url": _watchpoint_log_url(pod_name, datetime.now(UTC)),
        }

    if correlation_rows:
        context.correlations_sheets_client.append_rows(correlation_rows)
    return {
        "audit_status": success_status,
        "overall_status": FINAL,
        "error_detail": "",
        "error_pod": "",
        "error_log_url": "",
        **coverage_fields,
    }


def _park_row(
    ground_truth: GroundTruth, reference_id: str, incident_context: dict[str, str]
) -> dict[str, Any]:
    return {
        "reference_id": reference_id,
        "audit_status": NEEDS_HUMAN_REVIEW,
        "overall_status": ONGOING,
        "change_related": ground_truth.change_related,
        "ownership": ground_truth.ownership,
        "cited_identifier": ground_truth.cited_identifier,
        "quote_span": ground_truth.quote_span,
        "reasoning": ground_truth.reasoning,
        # "" (not False) when the LLM couldn't tell either -- a human decides.
        "source_traceable": (
            ground_truth.source_traceable
            if ground_truth.source_traceable is not None
            else ""
        ),
        "gold_entity_type": "",
        "gold_entity_id": "",
        "human_gold_entity": "",
        # No gold entity to check coverage against yet -- not applicable.
        "root_cause_covered_postmortem": "",
        "root_cause_covered_investigation": "",
        "error_detail": "",
        "error_pod": "",
        "error_log_url": "",
        **incident_context,
    }


def process_entity(
    context: RootCauseAuditContext, entity: dict[str, Any]
) -> dict[str, Any]:
    """Extract, verify, and (if verified) score a newly-terminal incident."""
    reference_id = entity["reference_id"]
    incident_id = entity["incident_id"]
    entity_created_at = entity["created_at"]

    payload = context.incidentio_client.get_incident(incident_id)
    incident_data = payload.get("incident", {})
    incident, _, _, _ = build_incident_from_payload(incident_data)
    incident_context = _build_incident_context(
        incident_data, incident, entity_created_at
    )
    updates = context.incidentio_client.list_incident_updates(incident_id)
    messages = [u["message"] for u in updates if u.get("message")]
    # incident.io remains the eligibility source (see find_eligible_entities),
    # but the Slack-channel summary is Matik's own OpsBot-derived enrichment,
    # only ever stored in our DB -- this is a narrow per-incident lookup, not
    # the kind of unfiltered eligibility read ADR 017 warns against. Surfaced
    # as its own reviewer-facing column, not just fed into extraction, so a
    # human can see everything the LLM saw, not just incident.io's summary.
    db_incident = context.incident_dao.find_incident(reference_id)
    channel_summary = db_incident.incident_channel_summary if db_incident else None
    incident_context["channel_summary"] = channel_summary or ""
    source_text = build_source_text(
        incident_data.get("summary"), messages, channel_summary
    )

    ground_truth = extract_ground_truth(
        context.llm_client,
        source_text,
        reference_id,
        context.ground_truth_extraction_prompt,
        entity_created_at,
    )
    gold_entity = verify_ground_truth(ground_truth, context)
    if gold_entity is None:
        return _park_row(ground_truth, reference_id, incident_context)

    result_fields = _run_classification(
        context, reference_id, entity_created_at, gold_entity, incident, LLM_REVIEWED
    )
    return {
        "reference_id": reference_id,
        "change_related": ground_truth.change_related,
        "ownership": ground_truth.ownership,
        "cited_identifier": ground_truth.cited_identifier,
        "quote_span": ground_truth.quote_span,
        "reasoning": ground_truth.reasoning,
        # A verified root cause is always traceable, regardless of what the
        # LLM's own source_traceable judgment said.
        "source_traceable": True,
        "gold_entity_type": gold_entity.entity_type,
        "gold_entity_id": gold_entity.entity_id,
        "human_gold_entity": "",
        **incident_context,
        **result_fields,
    }


def _retry_error_row(
    context: RootCauseAuditContext, reference_id: str, row: dict[str, Any]
) -> dict[str, Any] | None:
    """Retry a correlation fetch that failed on a previous run.

    The gold entity (or its confirmed absence) was already established
    before the fetch failed, so this resumes straight from there --
    ``gold_entity_type``/``gold_entity_id`` being blank means the row was a
    confirmed-no-root-cause case, not an unresolved one (an ``error`` row
    always has one of the two paths already decided)."""
    incident = context.incident_dao.find_incident(reference_id)
    if incident is None:
        logger.warning(
            "error row's incident no longer found, skipping retry",
            reference_id=reference_id,
        )
        return None

    gold_entity_type = str(row.get("gold_entity_type") or "")
    gold_entity_id = str(row.get("gold_entity_id") or "")
    if gold_entity_type and gold_entity_id:
        gold_entity = VerifiedEntity(
            entity_type=gold_entity_type, entity_id=gold_entity_id
        )
        resolved_status = (
            HUMAN_REVIEWED if row.get("human_gold_entity") else LLM_REVIEWED
        )
        result_fields = _run_classification(
            context,
            reference_id,
            incident.created_at,
            gold_entity,
            incident,
            resolved_status,
        )
    else:
        result_fields = _run_classification(
            context, reference_id, incident.created_at, None, incident, HUMAN_REVIEWED
        )
    return {**row, **result_fields}


def rescore_entity(
    context: RootCauseAuditContext, row: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve an ``ongoing`` row once it's newly retryable, or once a human
    has acted on it.

    Three ways an ``ongoing`` row resolves:
    1. It's an ``error`` row (correlation fetch failed on a prior run) --
       retried here every run, resuming from whatever gold entity (or its
       confirmed absence) was already established. No re-extraction.
    2. A human sets ``audit_status`` to ``human_reviewed`` with
       ``human_gold_entity`` filled in -- verified here the same way LLM
       extraction is, no special trust just because a human typed it.
    3. A human sets ``audit_status`` to ``human_reviewed`` with
       ``human_gold_entity`` left blank -- confirms no PR/TCMR root cause
       exists; correlations are classified by service overlap instead.

    ``audit_status == human_reviewed`` is the one signal that a human is done
    with this row, whether or not they found a root cause -- a row is left
    untouched until then, even with ``human_gold_entity`` already filled in.
    """
    if row.get("overall_status") != ONGOING:
        return None

    reference_id = row["reference_id"]

    if row.get("audit_status") == ERROR:
        return _retry_error_row(context, reference_id, row)

    if row.get("audit_status") != HUMAN_REVIEWED:
        return None

    human_gold_entity = str(row.get("human_gold_entity") or "").strip()

    if human_gold_entity:
        ground_truth = GroundTruth(
            change_related=True,
            ownership="airbnb",
            cited_identifier=human_gold_entity,
            quote_span=None,
            reasoning="human-provided during review",
        )
        gold_entity = verify_ground_truth(ground_truth, context)
        if gold_entity is None:
            logger.info(
                "human-provided gold entity did not verify, still ongoing",
                reference_id=reference_id,
            )
            return None

        incident = context.incident_dao.find_incident(reference_id)
        if incident is None:
            logger.warning(
                "ongoing row's incident no longer found, skipping rescore",
                reference_id=reference_id,
            )
            return None

        result_fields = _run_classification(
            context,
            reference_id,
            incident.created_at,
            gold_entity,
            incident,
            HUMAN_REVIEWED,
        )
        return {
            **row,
            "gold_entity_type": gold_entity.entity_type,
            "gold_entity_id": gold_entity.entity_id,
            # A verified root cause is always traceable, overriding whatever
            # was set (or left blank) while this row was parked.
            "source_traceable": True,
            **result_fields,
        }

    incident = context.incident_dao.find_incident(reference_id)
    if incident is None:
        logger.warning(
            "ongoing row's incident no longer found, skipping rescore",
            reference_id=reference_id,
        )
        return None

    result_fields = _run_classification(
        context, reference_id, incident.created_at, None, incident, HUMAN_REVIEWED
    )
    return {
        **row,
        "gold_entity_type": "",
        "gold_entity_id": "",
        **result_fields,
    }
