"""Scribe service configuration."""

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class ScribeQueueConfig(SQLModel):
    """Configuration for a single SQS queue pair."""

    queue_url: str = Field(..., description="SQS input queue URL")
    dlq_url: str = Field(
        ..., description="Dead letter queue URL for non-retryable failures"
    )
    max_receive_count: int = Field(
        default=5,
        description=(
            "SQS redrive policy maxReceiveCount for this queue. Used to detect "
            "when a message is on its final delivery attempt so a DLQ metric can "
            "be recorded before SQS silently redrives it."
        ),
    )


class ScribeConfig(SQLModel):
    """Configuration for the Scribe service.

    Each deployment mounts a config file with a single queue entry.

    Example YAML:

        scribe:
          queue:
            queue_url: https://sqs.us-east-1.amazonaws.com/123/scrb-high-queue
            dlq_url:   https://sqs.us-east-1.amazonaws.com/123/scrb-high-dlq
          sqs_queue_region: us-east-1
          max_concurrent_writes: 10
    """

    queue: ScribeQueueConfig = Field(
        ...,
        description="SQS queue pair for this deployment.",
    )

    # SQS connection
    sqs_queue_region: str | None = Field(
        default=None,
        description="AWS region for all SQS queues (defaults to us-east-1)",
    )

    # SQS polling (shared across all queues)
    sqs_max_messages: int = Field(
        default=10, description="Max messages per SQS receive call"
    )
    sqs_wait_time_seconds: int = Field(
        default=20, description="SQS long-poll wait time in seconds"
    )
    sqs_visibility_timeout: int = Field(
        default=300, description="SQS message visibility timeout in seconds"
    )
    sqs_poll_error_delay: int = Field(
        default=5,
        description="Seconds to wait after a poll error before retrying",
    )

    # Concurrency
    max_concurrent_writes: int = Field(
        default=10, description="Max concurrent DB writes per pod"
    )

    # Enrichment ordering
    enrichment_base_not_found_delay: int = Field(
        default=600,
        description=(
            "Visibility timeout in seconds applied when an enrichment message arrives "
            "before its base record exists. The message stays hidden for this duration "
            "before being redelivered, giving the base write time to land. "
            "SQS maximum is 43200 (12 hours)."
        ),
    )

    # Batching
    batch_max_messages: int = Field(
        default=25,
        description=(
            "Flush the in-process buffer when it reaches this many messages. "
            "Per-lane override in YAML (high: 25, medium: 50, low: 100)."
        ),
    )
    batch_flush_interval_ms: int = Field(
        default=500,
        description=(
            "Flush the in-process buffer when the oldest buffered message exceeds "
            "this age in milliseconds. Per-lane override in YAML "
            "(high: 500, medium: 2000, low: 5000)."
        ),
    )

    @field_validator("batch_max_messages", "batch_flush_interval_ms", mode="before")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        """Both batch knobs must be strictly positive."""
        if int(v) <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("sqs_queue_region", mode="before")
    @classmethod
    def normalize_empty_strings(cls, v: str | None) -> str | None:
        """Treat empty string as None so defaults apply."""
        if v is not None and v.strip() == "":
            return None
        return v

    @property
    def region(self) -> str:
        """Resolved AWS region, falling back to us-east-1."""
        return self.sqs_queue_region or "us-east-1"
