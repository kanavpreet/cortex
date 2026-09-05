"""MCP server configuration."""

from sqlmodel import Field, SQLModel


class McpConfig(SQLModel):
    """MCP server configuration.

    Note: No table=True, this is a configuration model only.
    """

    spec_refresh_interval_minutes: int = Field(
        default=5,
        description="Interval in minutes between OpenAPI spec refreshes",
    )

    http_session_idle_timeout_seconds: int = Field(
        default=1800,
        description="Idle timeout in seconds for Streamable HTTP sessions before reaping. "
        "Sessions that receive no requests for this duration are terminated. "
        "Must comfortably exceed any SSE polling retry interval.",
    )
