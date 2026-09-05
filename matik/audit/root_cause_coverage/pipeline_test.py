"""Tests for the AuditSpec callables wiring ground truth, correlation
retrieval, and classification together."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import audit.root_cause_coverage.pipeline as pipeline_mod
from audit.root_cause_coverage.pipeline import (
    ERROR,
    HUMAN_REVIEWED,
    LLM_REVIEWED,
    NEEDS_HUMAN_REVIEW,
    RootCauseAuditContext,
    _dedupe_across_buckets,
    _enrich_correlation_links,
    _fetch_correlations,
    find_eligible_entities,
    process_entity,
    rescore_entity,
)
from audit.sheet_sync import FINAL, ONGOING
from common.clients.incidentio_client import IncidentWithRawFields
from common.daos.ghe_pr_dao import GHEPullRequestWithRepo
from common.models.ghe_pr import GHEPullRequest
from common.models.incidentio_incident import IncidentIOIncident
from common.models.root_cause_audit import GroundTruth, VerifiedEntity

_BIZTECH_INCIDENT_TYPE_ID = "01HDCV644PMEQQTNKB4MNKTMSM"


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of pipeline._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(pipeline_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


def _incident(
    reference_id: str = "INC-1234",
    root_cause_service: str | None = None,
    affected_services: list[str] | None = None,
    status_category: str | None = "closed",
) -> IncidentIOIncident:
    return IncidentIOIncident(
        incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
        reference_id=reference_id,
        severity="Sev-1",
        slack_channel_id="C1234567890",
        status="closed",
        status_category=status_category,
        visibility="public",
        created_at=datetime(2026, 8, 1, 12, 0, 0),
        reported_at=datetime(2026, 8, 1, 12, 0, 0),
        updated_at=datetime(2026, 8, 1, 12, 30, 0),
        root_cause_service=root_cause_service,
        affected_services=affected_services,
    )


def _context(**overrides: object) -> RootCauseAuditContext:
    """Build a context from mocks. Callers keep their own references to the
    mocks they pass in for configuration/assertions -- ``context.foo`` reads
    back as the field's declared type, not ``MagicMock``, per mypy."""
    defaults: dict[str, object] = {
        "incident_dao": MagicMock(),
        "correlation_dao": MagicMock(),
        "ghe_pr_dao": MagicMock(),
        "incidentio_client": MagicMock(),
        "ghe_client": MagicMock(),
        "jira_client": MagicMock(),
        "llm_client": MagicMock(),
        "braintrust_client": MagicMock(),
        "correlations_sheets_client": MagicMock(),
        "ground_truth_extraction_prompt": "a ground truth prompt",
        "incident_type_ids": [_BIZTECH_INCIDENT_TYPE_ID],
    }
    defaults.update(overrides)
    return RootCauseAuditContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _dedupe_across_buckets
# ---------------------------------------------------------------------------


def test_dedupe_across_buckets_keeps_investigation_row_for_shared_entity() -> None:
    """postmortem's correlation table is an upserted accumulation of every
    rerun ever done, so an entity caught during investigation is always still
    there at postmortem too -- only the investigation-time row should survive."""
    investigation_row = {
        "time_bucket": "investigation",
        "entity_type": "github_pr",
        "entity_id": "120198",
    }
    postmortem_row = {
        "time_bucket": "postmortem",
        "entity_type": "github_pr",
        "entity_id": "120198",
    }

    result = _dedupe_across_buckets(
        {"investigation": [investigation_row], "postmortem": [postmortem_row]}
    )

    assert result == [investigation_row]


def test_dedupe_across_buckets_keeps_distinct_entities_from_both() -> None:
    investigation_row = {
        "time_bucket": "investigation",
        "entity_type": "github_pr",
        "entity_id": "120198",
    }
    postmortem_row = {
        "time_bucket": "postmortem",
        "entity_type": "jira_tcmr",
        "entity_id": "TCMR-23302",
    }

    result = _dedupe_across_buckets(
        {"investigation": [investigation_row], "postmortem": [postmortem_row]}
    )

    assert result == [investigation_row, postmortem_row]


def test_dedupe_across_buckets_keeps_postmortem_only_row() -> None:
    postmortem_row = {
        "time_bucket": "postmortem",
        "entity_type": "jira_tcmr",
        "entity_id": "TCMR-1",
    }

    result = _dedupe_across_buckets(
        {"investigation": [], "postmortem": [postmortem_row]}
    )

    assert result == [postmortem_row]


# ---------------------------------------------------------------------------
# _fetch_correlations
# ---------------------------------------------------------------------------


def test_fetch_correlations_opens_named_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """_fetch_correlations opens a span tagged with the reference_id."""
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    context = _context(
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        investigation_window_minutes=20,
    )

    _fetch_correlations(context, "INC-1234", datetime(2026, 8, 1, 12, 0, 0))

    (span,) = span_exporter.get_finished_spans()
    assert span.name == "fetch_correlations"
    assert span.attributes is not None
    assert span.attributes["reference_id"] == "INC-1234"


# ---------------------------------------------------------------------------
# _enrich_correlation_links
# ---------------------------------------------------------------------------


def test_enrich_correlation_links_adds_url_from_registered_source() -> None:
    """Uses the real registered github_pr spec -- only its DAO call is
    mocked -- to confirm the registry lookup and batching actually wire up,
    not just that some mock got called."""
    ghe_pr_dao = MagicMock()
    ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = []
    context = _context(ghe_pr_dao=ghe_pr_dao)
    rows = [{"entity_type": "github_pr", "entity_id": "120198"}]

    result = _enrich_correlation_links(rows, context)

    assert result[0]["url"] == ""
    ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.assert_called_once_with([120198])


def test_enrich_correlation_links_sets_url_when_dao_resolves_the_pr() -> None:
    ghe_pr_dao = MagicMock()
    ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = [
        GHEPullRequestWithRepo(
            pr=GHEPullRequest(
                pull_request_id=120198,
                pull_request_number=322,
                org_id=1,
                org_login="Airbnb-ITX",
                repo_id=1,
                repo_name="matik",
                merged=True,
                state="closed",
                locked=False,
                created_at=datetime(2026, 8, 1),
                target_branch_name="main",
            ),
            org="Airbnb-ITX",
            repo_name="matik",
        )
    ]
    context = _context(ghe_pr_dao=ghe_pr_dao)
    rows = [{"entity_type": "github_pr", "entity_id": "120198"}]

    result = _enrich_correlation_links(rows, context)

    assert result[0]["url"] == "https://github.airbnb.biz/Airbnb-ITX/matik/pull/322"


def test_enrich_correlation_links_blank_for_unregistered_entity_type() -> None:
    context = _context()
    rows = [{"entity_type": "pagerduty_change", "entity_id": "42"}]

    result = _enrich_correlation_links(rows, context)

    assert result[0]["url"] == ""


def test_enrich_correlation_links_blank_when_type_or_id_missing() -> None:
    context = _context()
    rows = [{"time_bucket": "postmortem"}]

    result = _enrich_correlation_links(rows, context)

    assert result[0]["url"] == ""


def test_enrich_correlation_links_batches_one_dao_call_per_type() -> None:
    ghe_pr_dao = MagicMock()
    ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = []
    context = _context(ghe_pr_dao=ghe_pr_dao)
    rows = [
        {"entity_type": "github_pr", "entity_id": "1"},
        {"entity_type": "github_pr", "entity_id": "2"},
    ]

    _enrich_correlation_links(rows, context)

    ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.assert_called_once_with([1, 2])


# ---------------------------------------------------------------------------
# find_eligible_entities
# ---------------------------------------------------------------------------


def test_find_eligible_entities_maps_incidents_to_dicts() -> None:
    incident = _incident()
    incident.closed_at = datetime.now(UTC).replace(tzinfo=None)
    incidentio_client = MagicMock()
    incidentio_client.list_all_incidents.return_value = [
        IncidentWithRawFields(incident=incident)
    ]
    context = _context(incidentio_client=incidentio_client)

    entities = find_eligible_entities(context)

    assert entities == [
        {
            "reference_id": "INC-1234",
            "incident_id": incident.incident_id,
            "created_at": incident.created_at,
        }
    ]
    call_kwargs = incidentio_client.list_all_incidents.call_args.kwargs
    assert call_kwargs["incident_type_ids"] == [_BIZTECH_INCIDENT_TYPE_ID]


def test_find_eligible_entities_filters_non_terminal_and_stale() -> None:
    """Only closed/canceled incidents settled within the lookback window are
    eligible -- the type/visibility filters are already applied by
    ``list_all_incidents`` itself, so this only tests the terminal-status and
    recency check."""
    terminal_recent = _incident(reference_id="INC-1", status_category="closed")
    terminal_recent.closed_at = datetime.now(UTC).replace(tzinfo=None)
    non_terminal = _incident(reference_id="INC-2", status_category="active")
    stale = _incident(reference_id="INC-3", status_category="closed")
    stale.closed_at = datetime(2020, 1, 1)

    incidentio_client = MagicMock()
    incidentio_client.list_all_incidents.return_value = [
        IncidentWithRawFields(incident=terminal_recent),
        IncidentWithRawFields(incident=non_terminal),
        IncidentWithRawFields(incident=stale),
    ]
    context = _context(incidentio_client=incidentio_client)

    entities = find_eligible_entities(context)

    assert [e["reference_id"] for e in entities] == ["INC-1"]


def test_find_eligible_entities_uses_configured_lookback() -> None:
    incidentio_client = MagicMock()
    incidentio_client.list_all_incidents.return_value = []
    context = _context(incidentio_client=incidentio_client, lookback_hours=6)

    find_eligible_entities(context)

    call_kwargs = incidentio_client.list_all_incidents.call_args.kwargs
    updated_at_gte = call_kwargs["updated_at_gte"]
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    # Roughly 6 hours before "now" -- sanity check the bound is a real date
    # string, not exact timing (day-granularity, so same-day is expected).
    assert updated_at_gte <= today


# ---------------------------------------------------------------------------
# process_entity
# ---------------------------------------------------------------------------


def _entity(reference_id: str = "INC-1234") -> dict[str, object]:
    return {
        "reference_id": reference_id,
        "incident_id": "01K3H5K30V3TECAF9G2HD1X5ZB",
        "created_at": datetime(2026, 8, 1, 12, 0, 0),
    }


def _incident_payload(
    root_cause_service: str | None = None,
    affected_services: list[str] | None = None,
) -> dict[str, object]:
    custom_field_entries = []
    if root_cause_service:
        custom_field_entries.append(
            {
                "custom_field": {"name": "Root Cause Service"},
                "values": [{"value_catalog_entry": {"name": root_cause_service}}],
            }
        )
    if affected_services:
        custom_field_entries.append(
            {
                "custom_field": {"name": "Affected Services"},
                "values": [
                    {"value_catalog_entry": {"name": s}} for s in affected_services
                ],
            }
        )
    return {
        "incident": {
            "summary": "a summary",
            "permalink": "https://app.incident.io/airbnb/incidents/xyz",
            "slack_channel_id": "C0BHCAE2R0W",
            "incident_status": {"category": "closed"},
            "custom_field_entries": custom_field_entries,
        }
    }


@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
@patch("audit.root_cause_coverage.pipeline.extract_ground_truth")
def test_process_entity_parks_when_unverified(
    mock_extract: MagicMock, mock_verify: MagicMock
) -> None:
    incidentio_client = MagicMock()
    incidentio_client.get_incident.return_value = _incident_payload()
    incidentio_client.list_incident_updates.return_value = [
        {"message": "traced to PR #1"}
    ]
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    correlations_sheets_client = MagicMock()
    context = _context(
        incidentio_client=incidentio_client,
        incident_dao=incident_dao,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_extract.return_value = GroundTruth(
        change_related=True,
        ownership="airbnb",
        cited_identifier="#1",
        quote_span="traced to PR #1",
        reasoning="cited",
    )
    mock_verify.return_value = None

    row = process_entity(context, _entity())

    assert row["audit_status"] == NEEDS_HUMAN_REVIEW
    assert row["overall_status"] == ONGOING
    assert row["reference_id"] == "INC-1234"
    assert row["gold_entity_type"] == ""
    assert row["incident_url"] == "https://app.incident.io/airbnb/incidents/xyz"
    assert row["slack_channel_url"] == "https://airbnb.slack.com/archives/C0BHCAE2R0W"
    assert row["description"] == "a summary"
    assert row["incident_date"] == "2026-08-01T12:00:00"
    assert row["incident_status"] == "closed"
    assert row["channel_summary"] == ""
    assert row["reasoning"] == "cited"
    assert row["root_cause_covered_postmortem"] == ""
    assert row["root_cause_covered_investigation"] == ""
    assert row["root_cause_service"] == ""
    assert row["affected_services"] == ""
    # GroundTruth didn't set an opinion -- left blank for a human to decide.
    assert row["source_traceable"] == ""
    correlations_sheets_client.append_rows.assert_not_called()


@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
@patch("audit.root_cause_coverage.pipeline.extract_ground_truth")
def test_process_entity_parks_with_llm_source_traceable_judgment(
    mock_extract: MagicMock, mock_verify: MagicMock
) -> None:
    """When a park-worthy incident still has an LLM opinion on
    source_traceable (e.g. a vendor outage on a public status page), that
    opinion passes straight through to the row, unmodified."""
    incidentio_client = MagicMock()
    incidentio_client.get_incident.return_value = _incident_payload()
    incidentio_client.list_incident_updates.return_value = []
    context = _context(incidentio_client=incidentio_client)
    mock_extract.return_value = GroundTruth(
        change_related=True,
        ownership="vendor",
        cited_identifier=None,
        quote_span=None,
        reasoning="vendor outage, reflected on their status page",
        source_traceable=True,
    )
    mock_verify.return_value = None

    row = process_entity(context, _entity())

    assert row["audit_status"] == NEEDS_HUMAN_REVIEW
    assert row["source_traceable"] is True


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
@patch("audit.root_cause_coverage.pipeline.extract_ground_truth")
def test_process_entity_scores_when_verified(
    mock_extract: MagicMock,
    mock_verify: MagicMock,
    mock_classify_matches: MagicMock,
    mock_classify_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    incidentio_client = MagicMock()
    incidentio_client.get_incident.return_value = _incident_payload(
        root_cause_service="powergrid"
    )
    incidentio_client.list_incident_updates.return_value = []
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    correlations_sheets_client = MagicMock()
    context = _context(
        incidentio_client=incidentio_client,
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_extract.return_value = GroundTruth(
        change_related=True,
        ownership="airbnb",
        cited_identifier="#1",
        quote_span="traced to PR #1",
        reasoning="cited",
    )
    gold = VerifiedEntity(entity_type="github_pr", entity_id="987")
    mock_verify.return_value = gold
    mock_classify_matches.return_value = MagicMock(
        root_cause_matches=[], unresolved=[], root_cause_covered=True
    )
    mock_classify_service.return_value = []
    mock_build_rows.side_effect = [
        [{"reference_id": "INC-1234", "time_bucket": "postmortem"}],
        [],
    ]

    row = process_entity(context, _entity())

    assert row["audit_status"] == LLM_REVIEWED
    assert row["overall_status"] == FINAL
    assert row["gold_entity_type"] == "github_pr"
    assert row["gold_entity_id"] == "987"
    assert row["incident_url"] == "https://app.incident.io/airbnb/incidents/xyz"
    assert row["reasoning"] == "cited"
    assert row["root_cause_covered_postmortem"] is True
    assert row["root_cause_covered_investigation"] is True
    assert row["root_cause_service"] == "powergrid"
    # A verified root cause is always traceable, regardless of GroundTruth's
    # own (here unset) source_traceable opinion.
    assert row["source_traceable"] is True
    correlations_sheets_client.append_rows.assert_called_once_with(
        [{"reference_id": "INC-1234", "time_bucket": "postmortem", "url": ""}]
    )
    # Relevance is classified by service overlap against the incident's own
    # services, regardless of a gold entity being present.
    incident_services_arg = mock_classify_service.call_args_list[0].args[1]
    assert incident_services_arg == {"powergrid"}


@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
@patch("audit.root_cause_coverage.pipeline.extract_ground_truth")
def test_process_entity_includes_db_channel_summary_in_source_text(
    mock_extract: MagicMock, mock_verify: MagicMock
) -> None:
    """incident.io stays the eligibility source, but the Slack-channel summary
    is Matik's own enrichment, only ever stored in the DB -- process_entity
    must look it up per-incident and feed it into extraction alongside
    incident.io's own summary and updates."""
    incidentio_client = MagicMock()
    incidentio_client.get_incident.return_value = _incident_payload()
    incidentio_client.list_incident_updates.return_value = []
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident()
    incident_dao.find_incident.return_value.incident_channel_summary = (
        "Slack thread mentions PR #42"
    )
    context = _context(incidentio_client=incidentio_client, incident_dao=incident_dao)
    mock_extract.return_value = GroundTruth(
        change_related=False,
        ownership="unknown",
        cited_identifier=None,
        quote_span=None,
        reasoning="none",
    )
    mock_verify.return_value = None

    row = process_entity(context, _entity())

    incident_dao.find_incident.assert_called_once_with("INC-1234")
    source_text = mock_extract.call_args.args[1]
    assert "Slack thread mentions PR #42" in source_text
    assert row["channel_summary"] == "Slack thread mentions PR #42"
    assert "a summary" in source_text


@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
@patch("audit.root_cause_coverage.pipeline.extract_ground_truth")
def test_process_entity_returns_error_row_when_correlation_fetch_fails(
    mock_extract: MagicMock,
    mock_verify: MagicMock,
    mock_classify_matches: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correlation-fetch failure (e.g. a Braintrust RBAC error) after a
    successful verification shouldn't be treated like an unresolved
    extraction -- it gets its own status so the already-verified gold entity
    isn't thrown away, and can be retried without re-extracting."""
    monkeypatch.setenv("K8S_POD_NAME", "matik-audit-root-cause-manual-abc123")
    incidentio_client = MagicMock()
    incidentio_client.get_incident.return_value = _incident_payload()
    incidentio_client.list_incident_updates.return_value = []
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    correlations_sheets_client = MagicMock()
    context = _context(
        incidentio_client=incidentio_client,
        incident_dao=incident_dao,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_extract.return_value = GroundTruth(
        change_related=True,
        ownership="airbnb",
        cited_identifier="#1",
        quote_span="traced to PR #1",
        reasoning="cited",
    )
    mock_verify.return_value = VerifiedEntity(entity_type="github_pr", entity_id="987")
    mock_classify_matches.side_effect = RuntimeError("403 Forbidden")

    row = process_entity(context, _entity())

    assert row["audit_status"] == ERROR
    assert row["overall_status"] == ONGOING
    assert row["gold_entity_type"] == "github_pr"
    assert row["gold_entity_id"] == "987"
    assert "403 Forbidden" in row["error_detail"]
    assert row["error_pod"] == "matik-audit-root-cause-manual-abc123"
    assert "matik-audit-root-cause-manual-abc123" in row["error_log_url"]
    correlations_sheets_client.append_rows.assert_not_called()


# ---------------------------------------------------------------------------
# rescore_entity
# ---------------------------------------------------------------------------


def _ongoing_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reference_id": "INC-1234",
        "audit_status": NEEDS_HUMAN_REVIEW,
        "overall_status": ONGOING,
        "human_gold_entity": "",
    }
    row.update(overrides)
    return row


def test_rescore_entity_skips_final_row() -> None:
    context = _context()
    row = {"reference_id": "INC-1234", "overall_status": FINAL}

    assert rescore_entity(context, row) is None


def test_rescore_entity_skips_when_not_yet_reviewed() -> None:
    context = _context()

    assert rescore_entity(context, _ongoing_row()) is None


def test_rescore_entity_returns_none_when_human_gold_entity_filled_but_not_marked_reviewed() -> (
    None
):
    """Filling in human_gold_entity alone isn't enough -- audit_status must
    also be flipped to human_reviewed, the one consistent signal a human is
    done with the row."""
    context = _context()
    row = _ongoing_row(audit_status=NEEDS_HUMAN_REVIEW, human_gold_entity="TCMR-1")

    assert rescore_entity(context, row) is None


@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
def test_rescore_entity_stays_ongoing_when_human_entry_does_not_verify(
    mock_verify: MagicMock,
) -> None:
    mock_verify.return_value = None
    context = _context()
    row = _ongoing_row(audit_status=HUMAN_REVIEWED, human_gold_entity="#999")

    assert rescore_entity(context, row) is None


@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
def test_rescore_entity_returns_none_when_incident_no_longer_found(
    mock_verify: MagicMock,
) -> None:
    mock_verify.return_value = VerifiedEntity(entity_type="github_pr", entity_id="1")
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    context = _context(incident_dao=incident_dao)
    row = _ongoing_row(audit_status=HUMAN_REVIEWED, human_gold_entity="#1")

    assert rescore_entity(context, row) is None


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
@patch("audit.root_cause_coverage.pipeline.verify_ground_truth")
def test_rescore_entity_scores_when_human_entry_verifies(
    mock_verify: MagicMock,
    mock_classify_matches: MagicMock,
    mock_classify_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    gold = VerifiedEntity(entity_type="jira_tcmr", entity_id="TCMR-1")
    mock_verify.return_value = gold
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident()
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    correlations_sheets_client = MagicMock()
    context = _context(
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_classify_matches.return_value = MagicMock(
        root_cause_matches=[], unresolved=[], root_cause_covered=False
    )
    mock_classify_service.return_value = []
    mock_build_rows.side_effect = [
        [],
        [{"reference_id": "INC-1234", "time_bucket": "investigation"}],
    ]
    row = _ongoing_row(audit_status=HUMAN_REVIEWED, human_gold_entity="TCMR-1")

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == HUMAN_REVIEWED
    assert result["overall_status"] == FINAL
    assert result["gold_entity_type"] == "jira_tcmr"
    assert result["gold_entity_id"] == "TCMR-1"
    assert result["root_cause_covered_postmortem"] is False
    assert result["root_cause_covered_investigation"] is False
    # A verified root cause is always traceable, overriding whatever was set
    # (or left blank) while this row was parked.
    assert result["source_traceable"] is True
    correlations_sheets_client.append_rows.assert_called_once_with(
        [{"reference_id": "INC-1234", "time_bucket": "investigation", "url": ""}]
    )


def test_rescore_entity_returns_none_when_no_citation_and_not_marked_reviewed() -> None:
    context = _context()
    row = _ongoing_row(audit_status=NEEDS_HUMAN_REVIEW, human_gold_entity="")

    assert rescore_entity(context, row) is None


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
def test_rescore_entity_confirms_no_root_cause_when_human_marks_reviewed(
    mock_classify_by_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident(
        root_cause_service="powergrid", affected_services=["biztech_office_infra"]
    )
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    correlations_sheets_client = MagicMock()
    context = _context(
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_classify_by_service.return_value = []
    mock_build_rows.side_effect = [
        [
            {
                "reference_id": "INC-1234",
                "time_bucket": "postmortem",
                "classification": "noise",
            }
        ],
        [],
    ]
    row = _ongoing_row(
        audit_status=HUMAN_REVIEWED, human_gold_entity="", source_traceable=False
    )

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == HUMAN_REVIEWED
    assert result["overall_status"] == FINAL
    assert result["gold_entity_type"] == ""
    assert result["gold_entity_id"] == ""
    assert result["root_cause_covered_postmortem"] == ""
    assert result["root_cause_covered_investigation"] == ""
    # Confirming "no root cause" doesn't touch whatever source_traceable was
    # already set to (by the LLM or a human) while parked.
    assert result["source_traceable"] is False
    incident_services_arg = mock_classify_by_service.call_args_list[0].args[1]
    assert incident_services_arg == {"powergrid", "biztech_office_infra"}
    correlations_sheets_client.append_rows.assert_called_once_with(
        [
            {
                "reference_id": "INC-1234",
                "time_bucket": "postmortem",
                "classification": "noise",
                "url": "",
            }
        ]
    )


def test_rescore_entity_no_root_cause_returns_none_when_incident_missing() -> None:
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    context = _context(incident_dao=incident_dao)
    row = _ongoing_row(audit_status=HUMAN_REVIEWED, human_gold_entity="")

    assert rescore_entity(context, row) is None


# ---------------------------------------------------------------------------
# rescore_entity: retrying an "error" row (correlation fetch failed before)
# ---------------------------------------------------------------------------


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
def test_rescore_entity_retries_error_row_with_gold_entity(
    mock_classify_matches: MagicMock,
    mock_classify_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    """An LLM-found gold entity that hit a correlation-fetch error retries
    straight from that gold entity -- no re-extraction -- and resolves to
    llm_reviewed since a human never provided it."""
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident()
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    correlations_sheets_client = MagicMock()
    context = _context(
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=correlations_sheets_client,
    )
    mock_classify_matches.return_value = MagicMock(
        root_cause_matches=[], unresolved=[], root_cause_covered=True
    )
    mock_classify_service.return_value = []
    mock_build_rows.side_effect = [[], []]
    row = _ongoing_row(
        audit_status=ERROR,
        human_gold_entity="",
        gold_entity_type="github_pr",
        gold_entity_id="120198",
        error_detail="correlation fetch/classification failed: 403 Forbidden",
        error_pod="matik-audit-root-cause-manual-abc123",
        error_log_url="https://grafana.a.musta.ch/a/watchpoint?...",
    )

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == LLM_REVIEWED
    assert result["overall_status"] == FINAL
    assert result["gold_entity_type"] == "github_pr"
    assert result["gold_entity_id"] == "120198"
    assert result["error_detail"] == ""
    assert result["error_pod"] == ""
    assert result["error_log_url"] == ""
    assert result["root_cause_covered_postmortem"] is True


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
def test_rescore_entity_retries_error_row_with_human_gold_entity(
    mock_classify_matches: MagicMock,
    mock_classify_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    """The same retry, but for a gold entity a human had provided -- resolves
    to human_reviewed, not llm_reviewed, since a human is who verified it."""
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident()
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    context = _context(
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=MagicMock(),
    )
    mock_classify_matches.return_value = MagicMock(
        root_cause_matches=[], unresolved=[], root_cause_covered=False
    )
    mock_classify_service.return_value = []
    mock_build_rows.side_effect = [[], []]
    row = _ongoing_row(
        audit_status=ERROR,
        human_gold_entity="TCMR-1",
        gold_entity_type="jira_tcmr",
        gold_entity_id="TCMR-1",
    )

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == HUMAN_REVIEWED
    assert result["overall_status"] == FINAL


@patch("audit.root_cause_coverage.pipeline.build_correlation_rows")
@patch("audit.root_cause_coverage.pipeline.classify_by_service_match")
def test_rescore_entity_retries_error_row_with_no_gold_entity(
    mock_classify_by_service: MagicMock,
    mock_build_rows: MagicMock,
) -> None:
    """A confirmed-no-root-cause row that hit a correlation-fetch error
    retries via the service-match path, not the gold-entity path, since
    blank gold_entity_type/id on an error row means that's what it was."""
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident(root_cause_service="powergrid")
    correlation_dao = MagicMock()
    correlation_dao.find_by_anchor.return_value = []
    braintrust_client = MagicMock()
    braintrust_client.get_investigation_time_correlations.return_value = []
    context = _context(
        incident_dao=incident_dao,
        correlation_dao=correlation_dao,
        braintrust_client=braintrust_client,
        correlations_sheets_client=MagicMock(),
    )
    mock_classify_by_service.return_value = []
    mock_build_rows.side_effect = [[], []]
    row = _ongoing_row(
        audit_status=ERROR,
        human_gold_entity="",
        gold_entity_type="",
        gold_entity_id="",
    )

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == HUMAN_REVIEWED
    assert result["overall_status"] == FINAL
    assert result["gold_entity_type"] == ""


@patch("audit.root_cause_coverage.pipeline.classify_root_cause_matches")
def test_rescore_entity_error_row_stays_error_when_retry_fails_again(
    mock_classify_matches: MagicMock,
) -> None:
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = _incident()
    context = _context(incident_dao=incident_dao)
    mock_classify_matches.side_effect = RuntimeError("403 Forbidden")
    row = _ongoing_row(
        audit_status=ERROR,
        human_gold_entity="",
        gold_entity_type="github_pr",
        gold_entity_id="120198",
    )

    result = rescore_entity(context, row)

    assert result is not None
    assert result["audit_status"] == ERROR
    assert result["overall_status"] == ONGOING
    assert "403 Forbidden" in result["error_detail"]


def test_rescore_entity_error_row_returns_none_when_incident_missing() -> None:
    incident_dao = MagicMock()
    incident_dao.find_incident.return_value = None
    context = _context(incident_dao=incident_dao)
    row = _ongoing_row(
        audit_status=ERROR, gold_entity_type="github_pr", gold_entity_id="1"
    )

    assert rescore_entity(context, row) is None
