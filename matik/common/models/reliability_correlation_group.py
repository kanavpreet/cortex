"""Reliability correlation group model for grouping related correlations."""

import json
from datetime import datetime
from typing import Any

from pydantic import field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Field, SQLModel


class FeedbackEntry(SQLModel):
    """A single user feedback event within a correlation group."""

    user_id: str = Field(..., description="User who provided feedback")
    value: str = Field(..., description="Feedback value: POSITIVE, NEGATIVE")
    timestamp: datetime = Field(
        ..., description="When the feedback was submitted (UTC)"
    )


class ReliabilityCorrelationGroup(SQLModel, table=True):
    """
    A correlation group is a container for related events believed to be connected.

    Maps to: reliability_correlation_groups table

    The group is anchored to a specific entity (e.g., an incident) and stores
    aggregated scoring, user feedback, and review status. Individual correlations
    are stored in the reliability_correlations table.

    All timestamps are stored as naive UTC.
    """

    __tablename__ = "reliability_correlation_groups"
    __table_args__ = (
        UniqueConstraint(
            "anchor_entity_id",
            name="uq_reliability_correlation_groups_anchor_entity",
        ),
        {"extend_existing": True},
    )

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # Unique business key for the anchor entity (e.g., INC-99)
    anchor_entity_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Unique business key for the anchor entity, e.g. INC-99",
    )

    # Anchor entity type
    anchor_type: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Type of entity this group is anchored to, e.g. incident, alert",
    )

    # Services from the anchor entity
    services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Backstage (GreenRoom) service identifiers from the anchor entity",
    )

    # Time window derived from individual correlations
    start_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Earliest start_time across the group's correlations",
    )
    end_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Latest end_time across the group's correlations",
    )

    # When the correlation group was created or last updated
    correlation_timestamp: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="When the correlation group was created or last updated (UTC)",
    )

    # Scoring
    base_score: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
        description="Aggregated score: min(avg(individual final_scores), GROUP_SCORE_CAP)",
    )
    final_score: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
        description="Score after applying user feedback. Defaults to base_score when NEUTRAL",
    )
    scoring_version: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Scoring algorithm version, e.g. incident_v1",
    )

    # User feedback
    feedback_list: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="List of user feedback events with user_id, value, and timestamp",
    )
    feedback_state: str = Field(
        default="NEUTRAL",
        sa_column=Column(String(50), nullable=False, default="NEUTRAL"),
        description="Derived feedback state: POSITIVE, NEUTRAL, or NEGATIVE",
    )

    # Review status
    review_status: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
        description="Whether the group has been reviewed",
    )
    last_reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Timestamp of the most recent review (UTC)",
    )

    # Row-level audit timestamps (DB-managed): when this DB row was written/updated.
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

    @field_validator("services", mode="before")
    @classmethod
    def parse_services(cls, v: Any) -> Any:
        """Parse JSON string to list if the DB returns a string for this column."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("feedback_list", mode="before")
    @classmethod
    def parse_feedback_list(cls, v: Any) -> Any:
        """Parse JSON string to list if the DB returns a string for this column."""
        if isinstance(v, str):
            return json.loads(v)
        return v
