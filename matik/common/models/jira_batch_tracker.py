"""JIRA batch tracker data model using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import Column, DateTime, Integer, String, Text, text
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class JiraBatchTracker(SQLModel, table=True):
    """Tracks batch processing status for JIRA ticket types.

    Maps to: jira_batch_tracker table
    Note: ticket_type is the primary key (one row per type).
    """

    __tablename__ = "jira_batch_tracker"
    __table_args__ = {"extend_existing": True}

    # TicketType is the primary key (tcmr, operational, alert)
    ticket_type: str = Field(
        sa_column=Column(String(50), primary_key=True, nullable=False),
        description="Ticket type (primary key)",
    )

    # Current batch window start (midnight UTC boundaries)
    batch_start: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Batch window start (UTC)",
    )

    # Current batch window end (midnight UTC boundaries)
    batch_end: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Batch window end (UTC)",
    )

    # Time diff used for batch window (typically 14 days)
    window_days: int = Field(
        sa_column=Column(Integer, nullable=False),
        description="Window size in days",
    )

    # Current status of the tracker
    # Values: None (initial/ready), "OK", "ERROR", "PROCESSING"
    status: str | None = Field(
        default=None,
        sa_column=Column(String(50), nullable=True),
        description="Tracker status: OK, ERROR, PROCESSING, or None",
    )

    # Optional error message if status is ERROR
    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Error message if status is ERROR",
    )

    # When the last batch was successfully completed
    last_processed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Last successful processing timestamp (UTC)",
    )

    # Automatic update timestamp
    updated_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Last update timestamp (UTC)",
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

    @field_validator(
        "batch_start",
        "batch_end",
        "last_processed_at",
        "updated_at",
        "row_created_at",
        "row_updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
