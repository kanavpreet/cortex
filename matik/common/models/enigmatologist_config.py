"""Enigmatologist service configuration."""

import re

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class EnigmatologistConfig(SQLModel):
    """Enigmatologist service configuration for SQS-based correlation requests.

    Used by both the API (to send messages) and the enigmatologist (to receive).
    No table=True, this is a configuration model only.
    """

    sqs_queue_url: str = Field(default="", description="SQS queue URL")
    # TODO: remove default once scribe is configured to trigger the enigmatologist
    scribe_queue_url: str = Field(
        default="",
        description="Scribe SQS queue URL for writing correlation results",
    )
    sqs_queue_region: str | None = Field(
        default=None, description="AWS region for SQS queue (defaults to us-east-1)"
    )

    # Worker concurrency
    max_concurrent_correlations: int = Field(
        default=5, description="Max concurrent correlation tasks"
    )

    # SQS polling parameters
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
        description="Seconds to wait after an SQS poll error before retrying",
    )

    # HTTP timeouts for Matik API calls (seconds)
    matik_api_read_timeout: float = Field(
        default=10.0, description="HTTP timeout for GET calls to the Matik API"
    )
    matik_api_write_timeout: float = Field(
        default=30.0, description="HTTP timeout for POST calls to the Matik API"
    )

    # Correlation lookback windows (hours)
    lookback_hours_github: int = Field(
        default=6, description="Hours to look back for GitHub PRs before incident"
    )
    lookback_hours_jira: int = Field(
        default=24, description="Hours to look back for Jira issues before incident"
    )

    # LLM provider selection
    llm_provider: str = Field(
        default="facade",
        description="LLM provider to use for correlation: 'facade' or 'bedrock'",
    )

    # LLM correlation prompt (required by the enigmatologist worker, not by the API)
    correlation_system_prompt: str = Field(
        default="", description="System prompt for the LLM incident correlation engine"
    )

    # LLM result filtering
    min_llm_score: float = Field(
        default=0.3, description="Minimum LLM score to keep a correlation match"
    )
    speculative_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns matched against LLM reasoning to filter out speculative matches",
    )
    speculative_max_score: float = Field(
        default=0.7,
        description=(
            "Speculative reasoning only drops a match scored below this. At or "
            "above it the score is trusted over hedging words (e.g. a 0.9 match "
            "whose reasoning says 'could cause' is kept, not filtered out)."
        ),
    )

    @field_validator("speculative_patterns", mode="before")
    @classmethod
    def validate_regex_patterns(cls, v: list[str]) -> list[str]:
        """Validate that all speculative_patterns are valid regular expressions."""
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f"invalid regex in speculative_patterns {pattern!r}: {e}"
                ) from e
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
