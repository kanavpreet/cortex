"""Common configuration shared across services."""

from sqlmodel import Field, SQLModel


class CommonConfig(SQLModel):
    """Common configuration shared across services.

    Note: No table=True, this is a configuration model only.
    """

    environment: str = Field(default="sandbox", description="Deployment environment")
    log_level: str = Field(default="INFO", description="Logging level")
    port: int = Field(default=8080, description="Service port")
