"""Reliability correlation model for individual correlations within a group."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Field, SQLModel


class ReliabilityCorrelation(SQLModel, table=True):
    """
    An individual correlation between a correlation group and an external event.

    Maps to: reliability_correlations table

    Each row represents one potential relationship between the group's anchor
    entity and a correlated event (e.g., a PR, TCMR, alert).

    All timestamps are stored as naive UTC.
    """

    __tablename__ = "reliability_correlations"
    __table_args__ = (
        UniqueConstraint(
            "anchor_entity_id",
            "entity_id",
            "correlation_type",
            name="uq_reliability_correlations_anchor_entity",
        ),
        {"extend_existing": True},
    )

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # FK to correlation group (business key, not DB FK)
    anchor_entity_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="FK to reliability_correlation_groups.anchor_entity_id, e.g. INC-99",
    )

    # Why this correlation exists
    correlation_type: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Why this correlation exists, e.g. SERVICE_MATCH, LLM",
    )

    # Correlated entity
    entity_type: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Type of correlated entity, e.g. github_pr, jira_tcmr, alert",
    )
    entity_id: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="External identifier of the correlated entity, e.g. PR-1, TCMR-1",
    )

    # Services associated with this correlated entity
    services: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Backstage (GreenRoom) service identifiers for the correlated entity",
    )

    # Event timestamps
    start_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Event start time (UTC)",
    )
    end_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Event end time (UTC)",
    )

    # Reasoning — human-readable explanation of why this correlation exists
    reasoning: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Human-readable explanation of why this correlation was made",
    )

    # Scoring
    base_score: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
        description="Raw confidence score from the scoring path (time-based or LLM-provided)",
    )
    final_score: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
        description="Score after applying the path-specific cap",
    )
    scoring_version: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Scoring algorithm version, e.g. incident_v1",
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
