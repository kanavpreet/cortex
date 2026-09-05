"""GHE (GitHub Enterprise) pull request data models using SQLModel."""

from datetime import datetime

from pydantic import field_serializer, field_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class GHEPullRequest(SQLModel, table=True):
    """Represents a GitHub Enterprise pull request.

    Maps to: ghe_pull_requests table
    """

    __tablename__ = "ghe_pull_requests"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "repo_id",
            "pull_request_number",
            name="uq_ghe_pr_org_repo_number",
        ),
        Index(
            "ix_ghe_pr_org_repo_number",
            "org_login",
            "repo_name",
            "pull_request_number",
        ),
        {"extend_existing": True},
    )

    # Primary key — the GitHub API internal PR ID is globally unique within a
    # GHE instance, so it serves as the natural primary key (no surrogate id).
    pull_request_id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, autoincrement=False),
        description="GitHub API internal PR ID (primary key)",
    )

    # PR number shown in URLs (e.g. 19 in /pull/19), unique within a repo
    pull_request_number: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="GitHub PR number shown in URLs",
    )

    # Organization identity (denormalized — no separate ghe_organizations table)
    org_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="GitHub organization ID",
    )
    org_login: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="GitHub organization login (slug, e.g. 'Airbnb')",
    )

    # Repository identity (denormalized — no separate ghe_repositories table)
    repo_id: int = Field(
        sa_column=Column(BigInteger, nullable=False, index=True),
        description="GitHub repository ID",
    )
    repo_name: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="GitHub repository name (slug, e.g. 'my-service')",
    )

    # Merged status of the pull request
    merged: bool = Field(
        sa_column=Column(Boolean, nullable=False),
        description="Whether PR was merged",
    )

    # State of the pull request
    state: str = Field(
        sa_column=Column(String(50), nullable=False),
        description="PR state: open or closed",
    )

    # Locked status of the pull request
    locked: bool = Field(
        sa_column=Column(Boolean, nullable=False),
        description="Whether PR is locked",
    )

    # CreatedAt is the timestamp when the pull request was created
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Creation timestamp (UTC)",
    )

    # ClosedAt is the timestamp when the pull request was closed
    closed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Close timestamp (UTC)",
    )

    # MergedAt is the timestamp when the pull request was merged
    merged_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Merge timestamp (UTC)",
    )

    # Target branch name of the pull request
    target_branch_name: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Target branch name",
    )

    # LLM Generated summary of the pull request description/body
    pull_request_summary: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="LLM-generated PR description summary",
    )

    # JIRA TCMR issue key parsed from the PR body/title (e.g. "TCMR-12345").
    # Logical reference to jira_issues.issue_key (no enforced FK — this schema
    # uses none).
    jira_tcmr_key: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="JIRA TCMR issue key (e.g. TCMR-12345), references jira_issues.issue_key",
    )

    # Soft delete timestamp
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Soft delete timestamp",
    )

    # Environment
    environment: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Environment",
    )

    # Hash of PR description for change detection
    description_hash: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="SHA256 hash of PR description for change detection",
    )

    last_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Last update timestamp",
    )

    # Services associated with this pull request
    services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="List of services associated with this PR",
    )

    # DLQ retry staleness guard: one entered_at column per write-group, set by
    # the write guard in common/daos/base_dao.py to the winning message's
    # entered_at, not the current time. NULL means no guard has applied yet.
    # See ADR 024-dlq-retry-staleness-guard.md.
    ghe_pr_base_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning ghe_pr base write (staleness guard)",
    )
    ghe_pr_enrichment_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="entered_at of the last winning ghe_pr enrichment write (staleness guard)",
    )

    # Row-level audit timestamps (DB-managed): when this DB row was written/updated.
    # Distinct from created_at / last_updated_at above, which are GitHub timestamps.
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
        "closed_at",
        "merged_at",
        "deleted_at",
        "last_updated_at",
        "ghe_pr_base_entered_at",
        "ghe_pr_enrichment_entered_at",
        "row_created_at",
        "row_updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)

    @field_serializer(
        "created_at",
        "closed_at",
        "merged_at",
        "deleted_at",
        "last_updated_at",
        "ghe_pr_base_entered_at",
        "ghe_pr_enrichment_entered_at",
        "row_created_at",
        "row_updated_at",
    )
    @classmethod
    def serialize_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Ensure timestamps are datetime objects during serialization."""
        return parse_timestamp_to_utc(v)
