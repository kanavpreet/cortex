"""API service configuration."""

from sqlmodel import Field, SQLModel


class ApiConfig(SQLModel):
    """API service configuration.

    Note: No table=True, this is a configuration model only.
    """

    api_endpoint: str = Field(
        default="http://matik-api:8080", description="API service endpoint URL"
    )
    service_secret: str | None = Field(
        default=None,
        description=(
            "Shared HMAC secret for service-to-service request signing "
            "(see common/utils/service_auth.py). Populated via secret-lair "
            "on every caller (historian/enricher/enigmatologist/mcp/"
            "integration-tests) and on the API itself for verification. "
            "None disables signing/verification entirely (e.g. local runs "
            "without the secret configured)."
        ),
    )
    enforce_service_signature: bool = Field(
        default=False,
        description=(
            "Server-side only: when False (shadow mode), matik-api logs "
            "whether each request's signature would have passed/failed but "
            "does not reject it. When True, an invalid or missing signature "
            "gets a 401. Meaningless on the client side."
        ),
    )
