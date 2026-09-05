"""Facade (LLM Fusion Hub) configuration."""

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class FacadeConfig(SQLModel):
    """Facade client configuration for LLM Fusion Hub.

    Note: No table=True, this is a configuration model only.
    """

    base_url: str = Field(
        default="https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai",
        description="Base URL for the LLM Fusion Hub endpoint",
    )
    resource_bucket: str = Field(
        default="prototype",
        description="Azure resource bucket: 'production' or 'prototype'",
    )
    default_model: str = Field(
        default="matik-sandbox-gpt-4o",
        description="Default model to use for chat completions",
    )
    api_version: str = Field(
        default="2024-12-01-preview",
        description="Azure API version query parameter",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed requests",
    )
    backoff_delays: list[float] = Field(
        default=[10.0, 20.0, 40.0],
        description="Delay durations in seconds between retry attempts",
    )
    iap_token: str | None = Field(
        default=None,
        description="IAP token for local development (not needed in Kubernetes)",
    )
    mock_mode: bool = Field(
        default=False,
        description="When True, use MockFacadeClient that returns canned responses (for local testing)",
    )

    @field_validator("mock_mode", mode="before")
    @classmethod
    def coerce_mock_mode(cls, v: bool | str | None) -> bool:
        """Coerce None or empty string to False (envsubst produces '' when var is unset)."""
        if v is None or v == "":
            return False
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)
