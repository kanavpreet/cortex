"""UAT Settings: resolved configuration for a target environment."""

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """
    Resolved UAT configuration for a target environment.

    All fields are derived from the environment registry (environments.py)
    and CLI options passed to pytest.
    """

    environment: str = Field(..., description="Target environment name")
    cell: str | None = Field(default=None, description="Target cell, or None for all")
    namespace: str = Field(..., description="Kubernetes namespace")
    api_base_url: str = Field(..., description="Base URL for matik-api HTTP calls")
    mcp_base_url: str = Field(..., description="Base URL for matik-mcp HTTP calls")
    facade_base_url: str = Field(..., description="Base URL for LLM Fusion Hub Facade (Azure OAI proxy)")
    bedrock_base_url: str = Field(..., description="Base URL for LLM Fusion Hub Bedrock proxy")
    facade_model: str = Field(..., description="Azure OAI deployment name for the Facade (environment-specific)")
    kubectl_context: str | None = Field(
        default=None,
        description="kubectl context name, or None to use current context",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers sent with every API request (e.g. auth tokens for devAccess URLs)",
    )
    service_secret: str | None = Field(
        default=None,
        description=(
            "Phase 1c shared secret for signing requests to matik-api "
            "(see helpers/service_signature.py). None disables signing — "
            "fine while enforce_service_signature is false server-side."
        ),
    )
