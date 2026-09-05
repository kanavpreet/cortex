"""Greenroom/Backstage configuration."""

from sqlmodel import Field, SQLModel


class GreenroomConfig(SQLModel):
    """Greenroom/Backstage configuration.

    Note: No table=True, this is a configuration model only.

    Authentication modes:
    1. Local (laptop) mode: Uses IAP token + Backstage token for human users
       - Requires: iap_token (get via: iap-auth https://developers.a.musta.ch)
       - Host defaults to: https://developers.a.musta.ch
    2. Kubernetes mode: Uses static Bearer token for service-to-service via AirMesh
       - Requires: api_token
       - Host defaults to: http://greenroom-production.greenroom-production:7007
    """

    api_token: str | None = Field(
        default=None, description="Greenroom API static Bearer token (for Kubernetes)"
    )
    host: str = Field(
        default="http://greenroom-production.greenroom-production:7007",
        description="Greenroom API host URL",
    )
    iap_token: str | None = Field(
        default=None,
        description="IAP token for local development (get via: iap-auth https://developers.a.musta.ch)",
    )
