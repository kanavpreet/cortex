"""Artifactory configuration."""

from sqlmodel import Field, SQLModel


class ArtifactoryConfig(SQLModel):
    """Artifactory configuration.

    Note: No table=True, this is a configuration model only.
    """

    base_url: str = Field(
        default="https://artifactory.airbnb.biz",
        description="Artifactory base URL",
    )
    api_token: str | None = Field(
        default=None,
        description="Bearer token for Artifactory authentication",
    )
