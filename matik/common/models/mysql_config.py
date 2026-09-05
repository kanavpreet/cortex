"""MySQL database configuration model using SQLModel."""

from typing import Any

from sqlmodel import Field, SQLModel


class MySQLConfig(SQLModel):
    """
    Represents MySQL database configuration.

    Note: No table=True, this is a configuration model only.

    Supports both traditional username/password authentication
    and AWS IAM authentication for RDS connections.
    """

    model_config = {"extra": "allow"}

    # Database engine type (e.g., "mysql")
    engine: str = Field(..., description="Database engine type, e.g. 'mysql'")

    # Database host/endpoint
    endpoint: str = Field(
        ..., description="Database host/endpoint, e.g. 'localhost' or RDS endpoint"
    )

    # Database port
    port: int = Field(default=3306, description="Database port, default 3306 for MySQL")

    # Username for authentication
    username: str = Field(..., description="Username for database authentication")

    # Password for traditional authentication (not used when use_iam is True)
    password: str | None = Field(
        default=None,
        description="Password for traditional authentication (not used when use_iam is True)",
    )

    # Database name
    database: str = Field(..., description="Database name to connect to")

    # AWS IAM authentication flag
    use_iam: bool = Field(
        default=False,
        description=(
            "Enable AWS IAM authentication for RDS connections. "
            "When True, an auth token is generated instead of using password."
        ),
    )

    # AWS region for IAM authentication
    region: str | None = Field(
        default=None,
        description="AWS region for IAM authentication (required when use_iam is True)",
    )

    # SSL/TLS mode configuration
    ssl_mode: str | None = Field(
        default=None,
        description="SSL/TLS mode: 'required', 'preferred', or 'disabled'",
    )

    # Canonical RDS endpoint for VPC endpoint scenarios
    canonical_endpoint: str | None = Field(
        default=None,
        description=(
            "Official RDS cluster endpoint for TLS validation and IAM token generation. "
            "Used when connecting through VPC endpoints where endpoint differs from "
            "the actual RDS cluster endpoint."
        ),
    )

    # AIRBNB-PROD IAM role ARN for cross-account access
    airbnbprod_role_arn: str | None = Field(
        default=None,
        description=(
            "First role in the chain: AIRBNB-PROD → CORPINFRA-PROD → database."
            "Chained assumption enforces least privilege instead of granting the app role direct access."
        ),
    )

    # CORPINFRA IAM role ARN for cross-account access
    corpinfra_role_arn: str | None = Field(
        default=None,
        description=(
            "ARN of the CORPINFRA-PROD IAM role to assume before connecting. "
            "Used for cross-account RDS access or additional permissions."
        ),
    )

    # Connection pool: maximum open connections
    max_open_conns: int = Field(
        default=0,
        description=(
            "Maximum number of open connections to the database. "
            "Default 0 (unlimited). Recommended to set based on environment."
        ),
    )

    # Connection pool: maximum idle connections
    max_idle_conns: int = Field(
        default=2,
        description=(
            "Maximum number of idle connections in the pool. "
            "Default 2. Recommended higher to keep connections warm."
        ),
    )

    # Connection pool: max connection lifetime in minutes
    conn_max_lifetime_minutes: int = Field(
        default=0,
        description=(
            "Maximum lifetime of a connection in minutes. "
            "Recommended 10 minutes for IAM auth (tokens expire after 15)."
        ),
    )

    # Connection pool: max idle time in minutes
    conn_max_idle_time_minutes: int = Field(
        default=0,
        description=(
            "Maximum time a connection can be idle before being closed. "
            "Recommended 5 minutes to keep the pool fresh."
        ),
    )

    # IAM token settings
    token_expiry_minutes: int | None = Field(
        default=15,
        description="IAM token lifetime in minutes. AWS RDS tokens are valid for 15 minutes.",
    )

    token_expiry_grace_minutes: int | None = Field(
        default=1,
        description="Minutes before token expiry to trigger refresh. Default 1 minute.",
    )

    def to_sqlalchemy_pool_kwargs(self) -> dict[str, Any]:
        """Convert pool settings to SQLAlchemy create_engine kwargs."""
        kwargs: dict[str, Any] = {}

        if self.max_idle_conns > 0:
            kwargs["pool_size"] = self.max_idle_conns

        if self.max_open_conns > 0:
            pool_size = kwargs.get("pool_size", 5)
            kwargs["max_overflow"] = max(0, self.max_open_conns - pool_size)

        if self.conn_max_lifetime_minutes > 0:
            kwargs["pool_recycle"] = self.conn_max_lifetime_minutes * 60

        if self.conn_max_idle_time_minutes > 0:
            kwargs["pool_timeout"] = self.conn_max_idle_time_minutes * 60

        return kwargs
