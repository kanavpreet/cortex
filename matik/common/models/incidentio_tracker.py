"""Incident.io tracker data model using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class IncidentIOTracker(SQLModel, table=True):
    """Tracks the sync state for the Incident.io connector.

    Maps to: incidentio_tracker table
    Note: This table uses a single-row pattern with id=1 constraint.
    """

    __tablename__ = "incidentio_tracker"
    __table_args__ = {"extend_existing": True}

    # Single-row constraint ID (always 1)
    id: int = Field(
        default=1,
        sa_column=Column(Integer, primary_key=True),
        description="Single row constraint ID (always 1)",
    )

    # Timestamp of the last successful sync
    timestamp: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        description="Timestamp of the last successful sync (UTC)",
    )

    # Current status of the tracker (OK or ERROR)
    status: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Tracker status: OK or ERROR",
    )

    # Optional error message if status is ERROR
    error_message: str | None = Field(
        default=None,
        sa_column=Column(String(3072), nullable=True),
        description="Error message if status is ERROR",
    )

    # Whether initial full sync has been completed
    initial_sync_complete: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
        description="Whether the initial full sync has been completed",
    )

    # The updated_at[gte] cursor used in the last sync attempt
    last_updated_at_cursor: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="The updated_at[gte] filter used in last sync where a failure occurred (UTC)",
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
        "timestamp",
        "last_updated_at_cursor",
        "row_created_at",
        "row_updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
