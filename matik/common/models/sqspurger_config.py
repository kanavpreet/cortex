"""SQS Purger configuration."""

from sqlmodel import Field, SQLModel


class SqsPurgerConfig(SQLModel):
    """SQS Purger configuration.

    Note: No table=True, this is a configuration model only.
    """

    region: str = Field(default="us-east-1", description="AWS region for SQS")
    queue_urls: list[str] = Field(
        default_factory=list, description="List of SQS queue URLs to purge"
    )
