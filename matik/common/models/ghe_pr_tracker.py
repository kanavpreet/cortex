"""GHE PR tracker data model for incremental crawling using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import BigInteger, Column, DateTime, Integer
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class GHEPRTracker(SQLModel, table=True):
    """Tracks the cutoff date for incremental PR crawling per repository.

    Maps to: ghe_pr_tracker table
    """

    __tablename__ = "ghe_pr_tracker"
    __table_args__ = {"extend_existing": True}

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # GitHub organization ID
    org_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="GitHub organization ID",
    )

    # GitHub repository ID
    repo_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="GitHub repository ID",
    )

    # CutoffDate is the date after which PRs should be crawled
    cutoff_date: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True),
        description="Date after which PRs should be crawled (UTC)",
    )

    # PRsCrawledCount is the number of PRs crawled in the last successful crawl
    prs_crawled_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=True),
        description="Number of PRs crawled in last successful crawl",
    )

    # CreatedAt is the timestamp when this tracker record was created
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=True),
        description="Creation timestamp (UTC)",
    )

    # UpdatedAt is the timestamp when this tracker record was last updated
    updated_at: datetime = Field(
        sa_column=Column(DateTime, nullable=True),
        description="Last update timestamp (UTC)",
    )

    @field_validator(
        "cutoff_date",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
