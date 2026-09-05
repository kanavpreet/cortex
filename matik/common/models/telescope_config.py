"""Telescope metrics configuration."""

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from common.constants import DEFAULT_OTEL_ENDPOINT, DEFAULT_TELESCOPE_TIMEOUT_SECONDS


class TelescopeConfig(SQLModel):
    """Telescope metrics configuration.

    Note: No table=True, this is a configuration model only.

    Attributes:
        enabled: Whether Telescope metrics are enabled
        tenant_id: Telescope tenant ID for routing metrics
        push_interval_seconds: How often to push metrics (default: 60s)
        otel_endpoint: OTLP HTTP endpoint URL (default: local sidecar)
        timeout_seconds: Request timeout for metric exports (default: 30s)
        service_name: Name of the service emitting metrics (set at runtime)
        service_version: Version of the service (optional, set at runtime)
        environment: Deployment environment (optional, set at runtime)
    """

    enabled: bool | None = Field(
        default=False, description="Whether Telescope metrics are enabled"
    )
    tenant_id: str | None = Field(default=None, description="Telescope tenant ID")
    push_interval_seconds: int | None = Field(
        default=60, description="Metrics push interval in seconds"
    )
    otel_endpoint: str | None = Field(
        default=DEFAULT_OTEL_ENDPOINT,
        description="OTLP HTTP endpoint URL for metrics export",
    )
    timeout_seconds: int | None = Field(
        default=DEFAULT_TELESCOPE_TIMEOUT_SECONDS,
        description="Request timeout for metric exports in seconds",
    )
    # Runtime configuration (not from YAML, set programmatically)
    service_name: str | None = Field(
        default=None, description="Service name (set at runtime)"
    )
    service_version: str | None = Field(
        default=None, description="Service version (set at runtime)"
    )
    environment: str | None = Field(
        default=None, description="Deployment environment (set at runtime)"
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, v: bool | None) -> bool:
        """Convert None to False (handles unset env vars from envsubst)."""
        return False if v is None else v

    @field_validator("push_interval_seconds", mode="before")
    @classmethod
    def validate_push_interval(cls, v: int | None) -> int:
        """Convert None to default (handles unset env vars from envsubst)."""
        return 60 if v is None else v

    @field_validator("otel_endpoint", mode="before")
    @classmethod
    def validate_otel_endpoint(cls, v: str | None) -> str:
        """Convert None or unsubstituted env var to default."""
        if v is None:
            return DEFAULT_OTEL_ENDPOINT
        # Handle unsubstituted env var like ${TELESCOPE_OTEL_ENDPOINT}
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            return DEFAULT_OTEL_ENDPOINT
        return v

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def validate_timeout(cls, v: int | None) -> int:
        """Convert None to default (handles unset env vars from envsubst)."""
        return DEFAULT_TELESCOPE_TIMEOUT_SECONDS if v is None else v
