"""LLM call tracing configuration."""

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class LLMTracingConfig(SQLModel):
    """Configuration for GenAI Studio OpenTelemetry tracing of production LLM calls.

    Note: No table=True, this is a configuration model only.

    Attributes:
        enabled: Whether LLM call tracing is enabled
        braintrust_project_id: Braintrust project ID traces are attributed to
        capture_content: Whether prompt/response content is logged on spans
        environment: Deployment environment tagged on every span; set at runtime
        service_name: Originating service tagged on every span; set at runtime
    """

    enabled: bool | None = Field(
        default=False, description="Whether LLM call tracing is enabled"
    )
    braintrust_project_id: str | None = Field(
        default=None, description="Braintrust project ID traces are attributed to"
    )
    capture_content: bool | None = Field(
        default=True,
        description="Whether prompt/response content (which can carry PII) is "
        "logged on spans, for both Facade and Bedrock",
    )
    environment: str | None = Field(
        default=None,
        description="Deployment environment (local/sandbox/staging/production), "
        "tagged on every span as deployment.environment; set at runtime",
    )
    service_name: str | None = Field(
        default=None,
        description="Originating service (enricher/enigmatologist), tagged on every "
        "span as service.name; set at runtime",
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, v: bool | None) -> bool:
        """Convert None to False (handles unset env vars from envsubst)."""
        return False if v is None else v

    @field_validator("capture_content", mode="before")
    @classmethod
    def validate_capture_content(cls, v: bool | None) -> bool:
        """Default to True when unset (handles unset env vars from envsubst)."""
        return True if v is None else v
