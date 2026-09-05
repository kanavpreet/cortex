"""Bedrock (LLM Fusion Hub) configuration."""

from sqlmodel import Field, SQLModel


class BedrockConfig(SQLModel):
    """Bedrock client configuration for LLM Fusion Hub AWS Bedrock proxy.

    Note: No table=True, this is a configuration model only.
    """

    base_url: str = Field(
        default="https://llm-fusion-hub.a.musta.ch/api/v2/proxy/aws/bedrock",
        description="Base URL for the LLM Fusion Hub Bedrock endpoint",
    )
    region: str = Field(
        default="us-west-2",
        description="AWS region for Bedrock requests",
    )
    default_model: str = Field(
        default="us.anthropic.claude-sonnet-4-20250514-v1:0",
        description="Default model to use for Bedrock Converse requests",
    )
    max_tokens: int = Field(
        default=4096,
        description="Maximum number of tokens in the response",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts for failed requests",
    )
    backoff_delays: list[float] = Field(
        default=[10.0, 20.0, 40.0],
        description="Delay durations in seconds between retry attempts",
    )
