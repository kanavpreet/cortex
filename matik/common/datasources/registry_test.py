"""Tests for the DataSourceSpec registry."""

from unittest.mock import MagicMock

from common.datasources import all_sources, get_source, register_source
from common.datasources.registry import DataSourceSpec
from common.models.ghe_pr import GHEPullRequest
from common.models.incidentio_incident import IncidentIOIncident
from common.models.jira_issue_record import JiraIssueRecord
from common.models.scribe_messages import (
    GHEPREnrichmentMessage,
    IncidentChannelSummaryEnrichmentMessage,
    IncidentIOEnrichmentMessage,
    JiraEnrichmentMessage,
)


def test_incidentio_spec_self_registered() -> None:
    """Importing the package registers the incidentio spec (side-effect import)."""
    spec = get_source("incidentio")
    assert spec.source_type == "incidentio"
    assert spec.record_model is IncidentIOIncident
    assert spec.enrichment_message_model is IncidentIOEnrichmentMessage


def test_incidentio_spec_columns() -> None:
    """The spec carries the exact upsert/LLM column contract the DAO relies on."""
    spec = get_source("incidentio")
    # LLM columns must be excluded from update_columns so a base upsert never
    # clobbers enrichment.
    assert set(spec.llm_columns) == {
        "root_cause_summary",
        "root_cause_summary_hash",
        "description_summary",
        "description_hash",
    }
    assert not set(spec.update_columns) & set(spec.llm_columns)
    # Conflict keys and DB-managed audit columns are always excluded from the
    # update set. Creation timestamps (created_at/reported_at) ARE refreshed
    # on re-write, since incident.io declares no exclude_columns for them.
    for excluded in (
        "incident_id",
        "reference_id",
        "row_created_at",
        "row_updated_at",
        "id",
    ):
        assert excluded not in spec.update_columns
    assert "created_at" in spec.update_columns
    assert "reported_at" in spec.update_columns
    # The incident-channel-summary LLM/hash columns, and both sibling
    # write-groups' staleness-guard timestamps, are owned elsewhere and must
    # never be clobbered by a regular Incident.io re-upsert.
    assert spec.exclude_columns == [
        "incident_channel_summary",
        "incident_channel_summary_hash",
        "incidentio_enrichment_entered_at",
        "incident_channel_summary_entered_at",
    ]
    assert spec.base_entered_at_column == "incidentio_base_entered_at"
    assert spec.enrichment_entered_at_column == "incidentio_enrichment_entered_at"
    assert "incidentio_base_entered_at" in spec.update_columns
    assert "incidentio_enrichment_entered_at" not in spec.update_columns
    assert "incident_channel_summary_entered_at" not in spec.update_columns
    for excluded in (
        "incident_channel_summary",
        "incident_channel_summary_hash",
    ):
        assert excluded not in spec.update_columns


def test_incidentio_hook_target_is_record() -> None:
    """Incident.io runs its enrichment hook on the re-fetched record."""
    spec = get_source("incidentio")
    assert spec.enrichment_hook_target == "record"
    assert spec.record_finder == "find_incident_by_id"
    assert spec.enrichment_key == "incident_id"


def test_incidentio_dao_factory_builds_dao() -> None:
    """The spec's dao_factory constructs the Incident.io DAO from (engine, metrics)."""
    from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO

    spec = get_source("incidentio")
    assert spec.dao_factory is not None
    dao = spec.dao_factory(MagicMock(), None)
    assert isinstance(dao, IncidentIOIncidentDAO)


def test_all_sources_includes_incidentio() -> None:
    assert "incidentio" in {s.source_type for s in all_sources()}


def test_register_and_get_roundtrip() -> None:
    """register_source / get_source round-trips a spec by source_type."""
    spec = DataSourceSpec(
        source_type="_test_source",
        record_model=IncidentIOIncident,
        conflict_keys=["incident_id"],
    )
    register_source(spec)
    assert get_source("_test_source") is spec


def test_exclude_columns_removes_from_update_columns() -> None:
    """A column added to exclude_columns is dropped from the derived update set."""
    spec = DataSourceSpec(
        source_type="_test_exclude",
        record_model=IncidentIOIncident,
        conflict_keys=["incident_id", "reference_id"],
        exclude_columns=["severity"],
    )
    assert "severity" not in spec.update_columns
    # A normal mutable column is still updatable.
    assert "status" in spec.update_columns
    # Conflict keys + DB-managed audit columns are excluded automatically.
    for auto_excluded in ("incident_id", "id", "row_created_at", "row_updated_at"):
        assert auto_excluded not in spec.update_columns


# ---------------------------------------------------------------------------
# GHE Pull Requests
# ---------------------------------------------------------------------------


def test_ghe_pr_spec_self_registered() -> None:
    """Importing the package registers the ghe_pr spec (side-effect import)."""
    spec = get_source("ghe_pr")
    assert spec.source_type == "ghe_pr"
    assert spec.record_model is GHEPullRequest
    assert spec.enrichment_message_model is GHEPREnrichmentMessage


def test_ghe_pr_spec_columns() -> None:
    """The spec carries the exact upsert/LLM column contract the DAO relies on.

    Pins the pre-migration hand-written ``_UPSERT_COLUMNS`` list exactly so the
    derived update set is a behavior-preserving replacement.
    """
    spec = get_source("ghe_pr")
    assert set(spec.llm_columns) == {"pull_request_summary", "description_hash"}
    assert not set(spec.update_columns) & set(spec.llm_columns)
    # Exact match against the former hand-maintained _UPSERT_COLUMNS.
    assert set(spec.update_columns) == {
        "pull_request_number",
        "org_login",
        "repo_name",
        "merged",
        "state",
        "locked",
        "closed_at",
        "merged_at",
        "last_updated_at",
        "target_branch_name",
        "jira_tcmr_key",
        "environment",
        "services",
        "deleted_at",
        "ghe_pr_base_entered_at",
    }
    assert spec.base_entered_at_column == "ghe_pr_base_entered_at"
    assert spec.enrichment_entered_at_column == "ghe_pr_enrichment_entered_at"
    assert "ghe_pr_enrichment_entered_at" not in spec.update_columns
    # PK + immutable identity keys + creation timestamp are excluded.
    for excluded in (
        "pull_request_id",
        "org_id",
        "repo_id",
        "created_at",
        "row_created_at",
        "row_updated_at",
    ):
        assert excluded not in spec.update_columns
    # ``deleted_at`` stays in the update set so a re-discovered PR is revived.
    assert "deleted_at" in spec.update_columns


def test_ghe_pr_hook_and_enrichment_config() -> None:
    """GHE PR runs its hook on the message and overwrites LLM columns with None."""
    spec = get_source("ghe_pr")
    assert spec.enrichment_hook_target == "message"
    assert spec.record_finder is None
    assert spec.enrichment_key == "pull_request_id"
    assert spec.enrichment_overwrites_with_none is True
    assert spec.base_column_flags == {}


def test_ghe_pr_dao_factory_builds_dao() -> None:
    """The spec's dao_factory constructs the GHE PR DAO from (engine, metrics)."""
    from common.daos.ghe_pr_dao import GHEPRDAO

    spec = get_source("ghe_pr")
    assert spec.dao_factory is not None
    dao = spec.dao_factory(MagicMock(), None)
    assert isinstance(dao, GHEPRDAO)


# ---------------------------------------------------------------------------
# JIRA
# ---------------------------------------------------------------------------


def test_jira_spec_self_registered() -> None:
    """Importing the package registers the jira spec (side-effect import)."""
    spec = get_source("jira")
    assert spec.source_type == "jira"
    assert spec.record_model is JiraIssueRecord
    assert spec.enrichment_message_model is JiraEnrichmentMessage


def test_jira_spec_columns() -> None:
    """The spec carries the exact upsert/LLM column contract the DAO relies on.

    Pins the pre-migration hand-written ``_UPSERT_COLUMNS`` (plus ``services``,
    which was appended when update_services=True) exactly.
    """
    spec = get_source("jira")
    assert set(spec.llm_columns) == {
        "issue_summary",
        "issue_comments_summary",
        "summary_hash",
        "comments_hash",
    }
    assert not set(spec.update_columns) & set(spec.llm_columns)
    # Exact match against the former _UPSERT_COLUMNS + "services".
    assert set(spec.update_columns) == {
        "issue_id",
        "ticket_type",
        "summary",
        "status_name",
        "tcmr_related_git_pr_link",
        "tcmr_related_services",
        "tcmr_planned_start_date",
        "tcmr_planned_end_date",
        "services",
        "jira_base_entered_at",
    }
    assert spec.base_entered_at_column == "jira_base_entered_at"
    assert spec.enrichment_entered_at_column == "jira_enrichment_entered_at"
    assert "jira_enrichment_entered_at" not in spec.update_columns
    # Unique key + creation timestamp + DB-managed columns are excluded.
    for excluded in (
        "issue_key",
        "created_at",
        "id",
        "row_created_at",
        "row_updated_at",
    ):
        assert excluded not in spec.update_columns


def test_jira_hook_and_base_column_flags() -> None:
    """JIRA runs its hook on the message and gates ``services`` on update_services."""
    spec = get_source("jira")
    assert spec.enrichment_hook_target == "message"
    assert spec.record_finder is None
    assert spec.enrichment_key == "issue_key"
    assert spec.enrichment_overwrites_with_none is False
    assert spec.base_column_flags == {"update_services": "services"}


def test_jira_dao_factory_builds_dao() -> None:
    """The spec's dao_factory constructs the JIRA DAO from (engine, metrics)."""
    from common.daos.jira_issues_dao import JiraIssuesDAO

    spec = get_source("jira")
    assert spec.dao_factory is not None
    dao = spec.dao_factory(MagicMock(), None)
    assert isinstance(dao, JiraIssuesDAO)


def test_all_sources_includes_github_and_jira() -> None:
    """The registry exposes all three specced sources."""
    types = {s.source_type for s in all_sources()}
    assert {"incidentio", "ghe_pr", "jira"} <= types


# ---------------------------------------------------------------------------
# Incident Channel Summary (OpsBot on-demand feed)
# ---------------------------------------------------------------------------


def test_incident_channel_summary_spec_self_registered() -> None:
    """Importing the package registers the incident_channel_summary spec."""
    spec = get_source("incident_channel_summary")
    assert spec.source_type == "incident_channel_summary"
    assert spec.record_model is IncidentIOIncident
    assert spec.enrichment_message_model is IncidentChannelSummaryEnrichmentMessage


def test_incident_channel_summary_spec_partial_update_wiring() -> None:
    """The spec is enrichment-only — no base write route at all, since the
    raw OpsBot channel summary is never persisted."""
    spec = get_source("incident_channel_summary")
    assert spec.base_message_model is None
    assert spec.base_partial_update is None
    assert spec.enrichment_key == "reference_id"
    assert spec.enrichment_hook_target == "record"
    assert spec.record_finder == "find_incident"
    assert set(spec.llm_columns) == {
        "incident_channel_summary",
        "incident_channel_summary_hash",
    }


def test_incident_channel_summary_dao_factory_binds_own_spec() -> None:
    """The dao_factory constructs the shared IncidentIOIncidentDAO, bound to
    this spec's source_type (not "incidentio") so partial updates and
    enrichment writes read the right enrichment_key/llm_columns."""
    from common.daos.incidentio_incident_dao import IncidentIOIncidentDAO

    spec = get_source("incident_channel_summary")
    assert spec.dao_factory is not None
    dao = spec.dao_factory(MagicMock(), None)
    assert isinstance(dao, IncidentIOIncidentDAO)
    assert dao._spec.source_type == "incident_channel_summary"


def test_all_sources_includes_incident_channel_summary() -> None:
    assert "incident_channel_summary" in {s.source_type for s in all_sources()}
