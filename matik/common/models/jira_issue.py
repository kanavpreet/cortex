"""JIRA issue data models using SQLModel.

Note: No table=True, these are API/transfer models only.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class Parent(SQLModel):
    """Represents a parent issue reference."""

    id: str | None = Field(default=None, description="Parent issue ID")
    key: str | None = Field(default=None, description="Parent issue key")


class Status(SQLModel):
    """Represents the status of an issue."""

    self: str = Field(..., description="Status self URL")
    id: str = Field(..., description="Status ID")
    name: str = Field(..., description="Status name, e.g. Open, In Progress, Done")


class Resolution(SQLModel):
    """Represents the resolution of an issue."""

    self: str = Field(..., description="Resolution self URL")
    id: str = Field(..., description="Resolution ID")
    name: str = Field(..., description="Resolution name, e.g. Done, Fixed, Won't Fix")


class Comment(SQLModel):
    """Represents a comment on an issue."""

    id: str | None = Field(default=None, description="Comment ID")
    self: str | None = Field(default=None, description="Comment self URL")
    name: str | None = Field(default=None, description="Comment name")
    body: str | None = Field(default=None, description="Comment body")
    created: str | None = Field(default=None, description="Created timestamp string")
    updated: str | None = Field(default=None, description="Updated timestamp string")


class Comments(SQLModel):
    """Represents a collection of comments."""

    comments: list[Comment] | None = Field(default=None, description="Comment list")


class IssueLinkType(SQLModel):
    """Represents the type of link between issues."""

    id: str | None = Field(default=None, description="Link type ID")
    self: str | None = Field(default=None, description="Link type self URL")
    name: str = Field(..., description="Link type name")
    inward: str = Field(..., description="Inward description")
    outward: str = Field(..., description="Outward description")


class IssueLink(SQLModel):
    """Represents a link between issues."""

    id: str | None = Field(default=None, description="Link ID")
    self: str | None = Field(default=None, description="Link self URL")
    type: IssueLinkType = Field(..., description="Link type")
    outward_issue: Issue | None = Field(
        default=None, description="Outward linked issue"
    )
    inward_issue: Issue | None = Field(default=None, description="Inward linked issue")
    comment: Comment | None = Field(default=None, description="Link comment")


class IssueFields(SQLModel):
    """Represents all fields of an issue."""

    summary: str | None = Field(default=None, description="Issue summary")
    environment: str | None = Field(default=None, description="Environment")
    status: Status | None = Field(default=None, description="Issue status")
    resolution: Resolution | None = Field(default=None, description="Issue resolution")
    created: datetime | None = Field(
        default=None, description="Created timestamp (UTC)"
    )
    updated: datetime | None = Field(
        default=None, description="Updated timestamp (UTC)"
    )
    duedate: datetime | None = Field(default=None, description="Due date (UTC)")
    resolutiondate: datetime | None = Field(
        default=None, description="Resolution date (UTC)"
    )
    labels: list[str] | None = Field(default=None, description="Issue labels")
    issuelinks: list[IssueLink] | None = Field(
        default=None, description="Linked issues"
    )
    comments: Comments | None = Field(default=None, description="Issue comments")
    parent: Parent | None = Field(default=None, description="Parent issue")

    @field_validator(
        "created",
        "updated",
        "duedate",
        "resolutiondate",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)


class Issue(SQLModel):
    """Represents a JIRA issue."""

    model_config = {"from_attributes": True}  # Enables ORM mode

    id: str | None = Field(default=None, description="Issue ID")
    self: str | None = Field(default=None, description="Issue self URL")
    key: str | None = Field(default=None, description="Issue key, e.g. PROJ-123")
    fields: IssueFields | None = Field(default=None, description="Issue fields")

    # LLM-generated fields
    issue_summary: str | None = Field(default=None, description="LLM-generated summary")
    issue_comments_summary: str | None = Field(
        default=None, description="LLM-generated comments summary"
    )

    # Hash fields for change detection (to avoid redundant LLM calls)
    summary_hash: str | None = Field(
        default=None,
        description="SHA256 hash of raw issue summary for change detection",
    )
    comments_hash: str | None = Field(
        default=None,
        description="SHA256 hash of aggregated comments for change detection",
    )

    # TCMR-specific custom fields
    tcmr_related_git_pr_link: str | None = Field(
        default=None, description="Related Git PR link"
    )
    tcmr_related_services: list[str] | None = Field(
        default=None, description="List of services associated with this TCMR"
    )
    tcmr_planned_start_date: datetime | None = Field(
        default=None, description="TCMR planned start date (UTC)"
    )
    tcmr_planned_end_date: datetime | None = Field(
        default=None, description="TCMR planned end date (UTC)"
    )

    @field_validator("tcmr_planned_start_date", "tcmr_planned_end_date", mode="before")
    @classmethod
    def parse_planned_timestamps(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize planned date timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)


# Allow forward references in IssueLink
IssueLink.model_rebuild()
