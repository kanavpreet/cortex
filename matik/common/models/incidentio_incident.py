"""Incident.io data models using SQLModel for validation and serialization."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class IncidentIOIncident(SQLModel, table=True):
    """
    Represents an incident from Incident.io with comprehensive metadata.

    Maps to: incidentio_incidents table

    This model captures all incident lifecycle stages, custom fields,
    and LLM-generated analysis. All timestamps are stored as naive UTC.

    Note: Field names renamed to match database columns:
    - root_cause_change_types (plural, was root_cause_change_type)
    - detection_methods (plural, was detection_method)
    """

    __tablename__ = "incidentio_incidents"
    __table_args__ = (
        UniqueConstraint("incident_id", "reference_id", name="uq_incident_reference"),
        {"extend_existing": True},
    )

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # Unique identifier for the incident
    incident_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique identifier, e.g. 01K3H5K30V3TECAF9G2HD1X5ZB",
    )

    # Reference to this incident, as displayed across the product
    reference_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Reference displayed across product, e.g. INC-1234",
    )

    # Human readable name of the severity
    severity: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Severity level, e.g. Sev-1",
    )

    # Slack channel ID where the incident is being managed
    slack_channel_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Slack channel ID, e.g. C1234567890",
    )

    # Current status of the incident
    status: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Current status: open, investigating, or closed, etc",
    )

    # Whether the incident is public or private
    visibility: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Visibility level: public or private",
    )

    # Automatically generated timestamp of when the incident was created
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Automatically generated creation timestamp (UTC)",
    )

    # Automatically generated timestamp that is exactly the same as CreatedAt
    reported_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Automatically generated report timestamp (UTC)",
    )

    # Automatically generated timestamp of last activity on the incident
    updated_at: datetime = Field(
        sa_column=Column(DateTime, nullable=True),
        description="Automatically generated last activity timestamp (UTC)",
    )

    #
    # Manually set timestamps, all can be null
    #

    # Timestamp of when the incident was accepted
    accepted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident was accepted, null if not yet accepted",
    )

    # Timestamp of when the incident was declined
    declined_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident was declined, null if never declined",
    )

    # Timestamp of when the incident was canceled
    canceled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident was canceled, null if never canceled",
    )

    # Timestamp of when the incident was resolved
    resolved_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident was resolved, null if not yet resolved",
    )

    # Timestamp of when the incident impact started
    impact_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident impact started, null if not yet defined",
    )

    # Timestamp of when the incident was closed
    closed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp when incident was closed, null if not yet closed",
    )

    #
    # Manually set custom fields, all can be null
    #

    # List of impacted parties
    impacted_parties: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Impacted parties: Guest, Host, Airfam, etc.",
    )

    # List of impacted core functions
    impacted_core_functions: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Impacted core functions: Atrium, Nova, etc.",
    )

    # Whether the core booking and hosting flow is impacted
    core_booking_hosting_flow_impacted: bool | None = Field(
        default=None,
        sa_column=Column(Boolean, nullable=True),
        description="Whether core booking and hosting flow is impacted",
    )

    # List of affected services
    affected_services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description='Affected services, e.g. ["a4w-common", "biztech_alertpipelines"]',
    )

    # Root cause service
    root_cause_service: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description='Root cause service, e.g. "a4w-common"',
    )

    # List of root cause change type
    root_cause_change_type: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Root cause change types: Code Deploy, Config Change, etc.",
    )

    # Root cause change type if not listed in root_cause_change_types
    root_cause_change_type_other: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Root cause change types free text",
    )

    # List of root cause change type config
    root_cause_change_type_config: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Root cause change type configs: Sitar, Trebuchet, etc.",
    )

    # The environment in which the incident occurred
    environment: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Environment: Production or Non-Production",
    )

    # List of detection methods used (renamed to plural to match DB)
    detection_methods: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Detection methods: alert, manual, or other",
    )

    # Link to the detection source
    detection_link: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Link to detection source, e.g. PagerDuty incident URL",
    )

    # Status category from Incident.io API (e.g. active, closed, post-incident)
    status_category: str | None = Field(
        default=None,
        sa_column=Column(String(50), nullable=True),
        description="Status category from Incident.io API, e.g. active, closed",
    )

    #
    # LLM-generated fields
    #

    # LLM-generated root cause summary
    root_cause_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated root cause summary",
    )

    # Hash of raw summary + resolution_statement to detect changes for root cause
    root_cause_summary_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="Hash of raw summary + resolution fields to skip root cause LLM regeneration if unchanged",
    )

    # LLM-generated description summary from incident name and summary
    description_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated description summary from incident name and summary",
    )

    # Hash of name + summary to detect changes for description summary
    description_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="Hash of incident name + summary to skip description LLM regeneration",
    )

    #
    # Incident channel summary (OpsBot on-demand feed): LLM output only,
    # named plainly (no `_llm` suffix) to match the root_cause_summary /
    # description_summary convention above, where the LLM output itself
    # owns the plain column name. The raw Slack-channel text OpsBot posts is
    # never persisted here — it only ever exists in the Enricher's SQS
    # message, mirroring how Incident.io's own raw summary/resolution_statement
    # fields are handled above. Written via partial updates only, never by
    # the regular Incident.io upsert path (see exclude_columns in
    # common/datasources/incidentio.py).
    #

    # LLM-generated summary of the incident channel; served to the MCP
    incident_channel_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated summary of the incident channel, served to the MCP",
    )

    # Hash of the raw OpsBot channel summary to detect changes for the incident channel summary
    incident_channel_summary_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="Hash of the raw OpsBot channel summary to skip LLM regeneration if unchanged",
    )

    #
    # DLQ retry staleness guard: one entered_at column per write-group, set by
    # the write guard in common/daos/base_dao.py to the winning message's
    # entered_at, not the current time. Distinct from row_updated_at below,
    # which is DB-managed and reflects when MySQL last touched the row rather
    # than which message won. NULL means no guard has applied yet. See ADR
    # 024-dlq-retry-staleness-guard.md.
    #

    # entered_at of the last winning incidentio base upsert
    incidentio_base_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning incidentio base write (staleness guard)",
    )

    # entered_at of the last winning incidentio enrichment write (root_cause_summary/description_summary)
    incidentio_enrichment_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning incidentio enrichment write (staleness guard)",
    )

    # entered_at of the last winning incident_channel_summary enrichment write
    incident_channel_summary_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning incident_channel_summary write (staleness guard)",
    )

    #
    # Row-level audit timestamps (DB-managed): when this DB row was written and
    # last updated. Distinct from the domain created_at/updated_at above, which
    # come from Incident.io.
    #
    row_created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
        description="DB row creation timestamp (UTC, DB-managed)",
    )
    row_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        description="DB row last-update timestamp (UTC, DB-managed)",
    )

    @field_validator(
        "created_at",
        "reported_at",
        "updated_at",
        "accepted_at",
        "declined_at",
        "canceled_at",
        "resolved_at",
        "impact_started_at",
        "closed_at",
        "incidentio_base_entered_at",
        "incidentio_enrichment_entered_at",
        "incident_channel_summary_entered_at",
        "row_created_at",
        "row_updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
