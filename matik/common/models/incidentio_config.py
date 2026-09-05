"""Incident.io API configuration."""

from sqlmodel import Field, SQLModel

# API constants (not configurable - changing would break client)
INCIDENTIO_API_VERSION = "v2"
INCIDENTIO_MAX_PAGE_SIZE = 500  # /v2/incidents API maximum
INCIDENTIO_UPDATES_MAX_PAGE_SIZE = 250  # /v2/incident_updates API maximum

# Default constants for configurable settings
DEFAULT_BASE_URL = "https://api.incident.io"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_BACKOFF_DELAYS = [2.0, 4.0, 8.0]
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_WRITE_BATCH_SIZE = 100
DEFAULT_CONSUMER_TIMEOUT_SECONDS = 60
DEFAULT_MAX_CONCURRENT_LLM_CALLS = 10


class IncidentIOConfig(SQLModel):
    """Incident.io API configuration.

    Note: No table=True, this is a configuration model only.
    """

    api_key: str = Field(..., description="Incident.io API key for authentication")
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        description="Incident.io API base URL",
    )
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        description="Default pagination page size (max 250)",
    )
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        description="Maximum number of retry attempts for failed requests",
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        description="Request timeout in seconds",
    )
    backoff_delays: list[float] = Field(
        default=DEFAULT_BACKOFF_DELAYS,
        description="Backoff delays in seconds between retry attempts",
    )
    start_date: str | None = Field(
        default=None,
        description="Start date for initial sync (YYYY-MM-DD format, e.g. 2024-01-01)",
    )
    lookback_days: int = Field(
        default=DEFAULT_LOOKBACK_DAYS,
        description="Number of days to look back for updates on subsequent runs",
    )
    write_batch_size: int | None = Field(
        default=None,
        description="Number of incidents per batch write to database",
    )
    consumer_timeout_seconds: int | None = Field(
        default=None,
        description="Timeout in seconds for consumer waiting on queue",
    )
    max_concurrent_llm_calls: int | None = Field(
        default=None,
        description="Maximum concurrent LLM calls for summarization",
    )
    root_cause_prompt: str = Field(
        description="System prompt for root cause summary LLM",
    )
    description_prompt: str = Field(
        description="System prompt for description summary LLM",
    )
    incident_type_ids: list[str] | None = Field(
        default=None,
        description="Filter synced incidents to these incident type IDs only (e.g. ['01HDCV644PMEQQTNKB4MNKTMSM']). Fetches all types if not set.",
    )
    sqs_queue_url: str | None = Field(
        default=None,
        description="Scribe historian SQS queue URL for incident writes",
    )
    sqs_queue_region: str = Field(
        default="us-east-1",
        description="AWS region for the Scribe historian SQS queue",
    )
