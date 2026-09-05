"""JIRA issue record models for database queries using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class JiraIssueRecord(SQLModel, table=True):
    """
    Represents a simplified JIRA issue record for database queries.

    Maps to: jira_issues table
    Note: This is a simplified model. The full table schema includes additional
    fields for status, resolution, parent, labels, and TCMR-specific data.
    """

    __tablename__ = "jira_issues"
    __table_args__ = (
        UniqueConstraint("issue_key", name="uq_jira_issue_key"),
        {"extend_existing": True},
    )

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # JIRA issue ID
    issue_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="JIRA issue ID",
    )

    # JIRA issue key (e.g., PROJ-123)
    issue_key: str = Field(
        sa_column=Column(String(255), nullable=False, index=True),
        description="JIRA issue key",
    )

    # Type of ticket (e.g., tcmr, operational)
    ticket_type: str = Field(
        sa_column=Column(String(50), nullable=False, index=True),
        description="Ticket type",
    )

    # Issue summary
    summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Issue summary",
    )

    # Status name
    status_name: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True, index=True),
        description="Status name",
    )

    # Created timestamp
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True),
        description="Creation timestamp (UTC)",
    )

    # LLM-generated summaries (persisted for caching)
    issue_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated issue summary",
    )
    issue_comments_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated comments summary",
    )

    # Hash fields for change detection (to avoid redundant LLM calls)
    summary_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="SHA256 hash of raw issue summary for change detection",
    )
    comments_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="SHA256 hash of aggregated comments for change detection",
    )

    # TCMR-specific custom fields
    tcmr_related_git_pr_link: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Related Git PR link from TCMR custom field",
    )
    tcmr_related_services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="List of services associated with this TCMR",
    )

    # Resolved Backstage service names
    services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Resolved Backstage service names",
    )

    # TCMR planned dates
    tcmr_planned_start_date: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="TCMR planned start date (UTC)",
    )
    tcmr_planned_end_date: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="TCMR planned end date (UTC)",
    )

    # DLQ retry staleness guard: one entered_at column per write-group, set by
    # the write guard in common/daos/base_dao.py to the winning message's
    # entered_at, not the current time. NULL means no guard has applied yet.
    # See ADR 024-dlq-retry-staleness-guard.md.
    jira_base_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning jira base write (staleness guard)",
    )
    jira_enrichment_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning jira enrichment write (staleness guard)",
    )

    # Row-level audit timestamps (DB-managed): when this DB row was written/updated.
    # Distinct from created_at above, which is the Jira issue creation date.
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
        "tcmr_planned_start_date",
        "tcmr_planned_end_date",
        "jira_base_entered_at",
        "jira_enrichment_entered_at",
        "row_created_at",
        "row_updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
