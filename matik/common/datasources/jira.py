"""JIRA data source spec.

Registered on import (see ``common/datasources/__init__.py``). This is the
single declaration of JIRA's write columns + Scribe routing; the model-derived
``BaseUpsertDAO`` and the generic Scribe handler read it instead of
hand-maintained per-source column lists and handler classes.
"""

from __future__ import annotations

from common.datasources.registry import DataSourceSpec, register_source
from common.models.jira_issue_record import JiraIssueRecord
from common.models.scribe_messages import JiraEnrichmentMessage


def _build_dao(engine, metrics=None):  # type: ignore[no-untyped-def]
    """Construct the JIRA issues DAO. Imported lazily to avoid an import cycle
    (the DAO imports this spec via ``get_source``)."""
    from common.daos.jira_issues_dao import JiraIssuesDAO

    return JiraIssuesDAO(engine, metrics)


register_source(
    DataSourceSpec(
        source_type="jira",
        record_model=JiraIssueRecord,
        # ``issue_key`` backs the unique index (uq_jira_issue_key) that the
        # ON DUPLICATE KEY UPDATE resolves against; it must stay stable on
        # conflict. The autoincrement PK ``id`` is DB-managed and excluded
        # automatically.
        conflict_keys=["issue_key"],
        # ``created_at`` is the Jira issue creation date — write-once, never
        # refreshed on re-write.
        # ``jira_enrichment_entered_at`` is owned by this same source's
        # enrichment write-group, not the base one — it isn't a field on
        # JiraEnrichmentMessage, so llm_columns can't auto-exclude it; a base
        # upsert must never reset the enrichment guard timestamp.
        exclude_columns=["created_at", "jira_enrichment_entered_at"],
        dao_factory=_build_dao,
        enrichment_message_model=JiraEnrichmentMessage,
        enrichment_key="issue_key",
        # JIRA's enrichment hook runs on the message directly (no re-fetch).
        enrichment_hook_target="message",
        # ``update_services=False`` on a base message (a crawl that skipped
        # Backstage service enrichment) drops the ``services`` column from the
        # update set so previously-resolved services are preserved.
        base_column_flags={"update_services": "services"},
        base_entered_at_column="jira_base_entered_at",
        enrichment_entered_at_column="jira_enrichment_entered_at",
    )
)
