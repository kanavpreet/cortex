"""Scribe service message models for SQS queue routing.

Each message carries two discriminator fields:
- source_type: identifies the target domain table
- message_type: 'base' for full record upserts, 'enrichment' for targeted LLM field updates

Base event messages carry the full domain model serialized as a flat JSON `data` dict
(matching model.model_dump() output). Enrichment messages carry only the unique identifier
and LLM-generated fields — they trigger a targeted UPDATE that does not overwrite base fields.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EnricherEnvelopeMixin(BaseModel):
    """Mixin for enrichment messages published by the Enricher.

    The Enricher publishes a nested envelope — ``{entity_id: {...}, updates:
    {...}, hashes: {...}}`` — but each enrichment message model expects flat
    top-level fields. This validator flattens the envelope so subclasses only
    need to declare their identifier + LLM/hash fields.
    """

    entered_at: datetime | None = Field(
        default=None,
        description=(
            "DLQ retry staleness guard (ADR 024): the time this fact entered "
            "Matik, assigned once at true origin (Chronicler/Historian) and "
            "propagated unchanged through the Enricher — never re-stamped. "
            "None on messages that predate the guard, which fall back to an "
            "unconditional write. Not an LLM/hash column: excluded from "
            "DataSourceSpec.llm_columns and written to "
            "spec.enrichment_entered_at_column instead."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_enricher_envelope(cls, data: Any) -> Any:
        """Flatten the enricher's nested envelope into the expected flat schema.

        When ``entity_id`` is present, merge entity_id, updates, and hashes into
        the top level. Otherwise the data is already flat and returned as-is.
        """
        if not isinstance(data, dict) or "entity_id" not in data:
            return data
        return {
            **data,
            **data.get("entity_id", {}),
            **data.get("updates", {}),
            **data.get("hashes", {}),
        }


# ---------------------------------------------------------------------------
# Incident.io
# ---------------------------------------------------------------------------


class IncidentIOBaseMessage(BaseModel):
    """Full Incident.io incident record write."""

    source_type: Literal["incidentio"] = "incidentio"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of IncidentIOIncident model fields"
    )
    entered_at: datetime | None = Field(
        default=None,
        description=(
            "DLQ retry staleness guard (ADR 024): the time this fact entered "
            "Matik, assigned once at true origin (Chronicler/Historian). "
            "None on messages that predate the guard (unconditional write)."
        ),
    )


class IncidentIOEnrichmentMessage(EnricherEnvelopeMixin):
    """Targeted LLM field update for an existing Incident.io incident."""

    source_type: Literal["incidentio"] = "incidentio"
    message_type: Literal["enrichment"] = "enrichment"
    incident_id: str = Field(
        ..., description="Incident.io internal ID, e.g. 01K3H5K30V3TECAF9G2HD1X5ZB"
    )
    root_cause_summary: str | None = Field(
        default=None, description="LLM-generated root cause summary"
    )
    root_cause_summary_hash: str | None = Field(
        default=None, description="SHA256 of summary + resolution_statement"
    )
    description_summary: str | None = Field(
        default=None, description="LLM-generated description summary"
    )
    description_hash: str | None = Field(
        default=None, description="SHA256 of incident name + summary"
    )


# ---------------------------------------------------------------------------
# Incident Channel Summary (OpsBot on-demand feed)
#
# Enrichment-only: patches the LLM/hash columns on the shared
# incidentio_incidents table. There is no base message — the raw
# Slack-channel text OpsBot posts is never persisted (it lives only in the
# Enricher's SQS message), so there's no non-sensitive record to write ahead
# of enrichment. See common/datasources/incident_channel_summary.py for the
# spec wiring.
# ---------------------------------------------------------------------------


class IncidentChannelSummaryEnrichmentMessage(EnricherEnvelopeMixin):
    """Targeted LLM field update for an incident's channel summary."""

    source_type: Literal["incident_channel_summary"] = "incident_channel_summary"
    message_type: Literal["enrichment"] = "enrichment"
    reference_id: str = Field(
        ..., description="Incident.io reference id, e.g. INC-1234"
    )
    incident_channel_summary: str | None = Field(
        default=None,
        description="LLM-generated summary of the incident channel, served to the MCP",
    )
    incident_channel_summary_hash: str | None = Field(
        default=None, description="SHA256 of the raw OpsBot channel summary"
    )


# ---------------------------------------------------------------------------
# GHE Pull Requests
# ---------------------------------------------------------------------------


class GHEPRBaseMessage(BaseModel):
    """Full GHE pull request record write.

    The flat ``data`` dict carries org/repo identity inline (org_id, org_login,
    repo_id, repo_name) — there are no separate org/repo tables to populate.
    """

    source_type: Literal["ghe_pr"] = "ghe_pr"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of GHEPullRequest model fields"
    )
    entered_at: datetime | None = Field(
        default=None,
        description=(
            "DLQ retry staleness guard (ADR 024): the time this fact entered "
            "Matik, assigned once at true origin (Chronicler/Historian). "
            "None on messages that predate the guard (unconditional write)."
        ),
    )


class GHEPREnrichmentMessage(EnricherEnvelopeMixin):
    """Targeted LLM field update for an existing GHE pull request.

    Keyed solely on ``pull_request_id`` — the GitHub PR ID is globally unique
    and is the primary key of ghe_pull_requests. The enricher may echo extra
    entity_id fields (org_id, repository_id); they are ignored on validation.
    """

    source_type: Literal["ghe_pr"] = "ghe_pr"
    message_type: Literal["enrichment"] = "enrichment"
    pull_request_id: int = Field(..., description="GitHub API internal PR ID")
    pull_request_summary: str | None = Field(
        default=None, description="LLM-generated PR description summary"
    )
    description_hash: str | None = Field(
        default=None, description="SHA256 of PR description"
    )


# ---------------------------------------------------------------------------
# JIRA
# ---------------------------------------------------------------------------


class JiraBaseMessage(BaseModel):
    """Full JIRA issue record write."""

    source_type: Literal["jira"] = "jira"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of JiraIssueRecord model fields"
    )
    update_services: bool = Field(
        default=True,
        description="When False, the services column is not overwritten on existing records",
    )
    entered_at: datetime | None = Field(
        default=None,
        description=(
            "DLQ retry staleness guard (ADR 024): the time this fact entered "
            "Matik, assigned once at true origin (Chronicler/Historian). "
            "None on messages that predate the guard (unconditional write)."
        ),
    )


class JiraEnrichmentMessage(EnricherEnvelopeMixin):
    """Targeted LLM field update for an existing JIRA issue."""

    source_type: Literal["jira"] = "jira"
    message_type: Literal["enrichment"] = "enrichment"
    issue_key: str = Field(..., description="JIRA issue key, e.g. OPS-123")
    issue_summary: str | None = Field(
        default=None, description="LLM-generated issue summary"
    )
    issue_comments_summary: str | None = Field(
        default=None, description="LLM-generated comments summary"
    )
    summary_hash: str | None = Field(
        default=None, description="SHA256 of issue description"
    )
    comments_hash: str | None = Field(
        default=None, description="SHA256 of aggregated comments"
    )


# ---------------------------------------------------------------------------
# GHE PR Tracker
# ---------------------------------------------------------------------------


class GHEPRTrackerBaseMessage(BaseModel):
    """Full GHE PR tracker record write."""

    source_type: Literal["ghe_pr_tracker"] = "ghe_pr_tracker"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of GHEPRTracker model fields"
    )


# ---------------------------------------------------------------------------
# Correlations (base only — no LLM enrichment)
# ---------------------------------------------------------------------------


class CorrelationBaseMessage(BaseModel):
    """Full reliability correlation record write."""

    source_type: Literal["correlation"] = "correlation"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of ReliabilityCorrelation model fields"
    )


class CorrelationGroupBaseMessage(BaseModel):
    """Full reliability correlation group record write."""

    source_type: Literal["correlation_group"] = "correlation_group"
    message_type: Literal["base"] = "base"
    data: dict[str, Any] = Field(
        ..., description="Flat JSON of ReliabilityCorrelationGroup model fields"
    )
