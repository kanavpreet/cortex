"""GHE org crawl tracker model for skipping dormant repos using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class GHEOrgCrawlTracker(SQLModel, table=True):
    """Tracks the last successful crawl time per organization.

    Used to skip repositories that have not been pushed to since the previous
    run: each run records the start time of its last fully-successful crawl,
    and the next run only lists/processes repos pushed on or after that time.

    Maps to: ghe_org_crawl_tracker table
    """

    __tablename__ = "ghe_org_crawl_tracker"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_ghe_org_crawl_tracker_org_id"),
        {"extend_existing": True},
    )

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # GitHub organization ID (one row per org)
    org_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="GitHub organization ID",
    )

    # Start time of the last fully-successful crawl for this org. Repos pushed
    # before this (minus a skew buffer) are skipped on the next run.
    last_crawled_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True),
        description="Start time of the last successful crawl (UTC)",
    )

    # CreatedAt is the timestamp when this record was created
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=True),
        description="Creation timestamp (UTC)",
    )

    # UpdatedAt is the timestamp when this record was last updated
    updated_at: datetime = Field(
        sa_column=Column(DateTime, nullable=True),
        description="Last update timestamp (UTC)",
    )

    @field_validator(
        "last_crawled_at",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
