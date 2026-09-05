"""JIRA API configuration."""

from sqlmodel import Field, SQLModel


class JiraConfig(SQLModel):
    """JIRA API configuration.

    Note: No table=True, this is a configuration model only.
    """

    base_url: str = Field(..., description="JIRA REST API base URL")
    username: str = Field(
        ..., description="JIRA username (usually service account LDAP)"
    )
    password: str = Field(
        ..., description="JIRA password (usually service account password)"
    )
    tcmr_jql_enabled: bool = Field(
        default=False, description="Flag to enable TCMR JQL processing"
    )
    tcmr_jql: str | None = Field(
        default=None, description="JQL query for fetching TCMR tickets"
    )
    operational_jql_enabled: bool = Field(
        default=False, description="Flag to enable operational JQL processing"
    )
    operational_jql: str | None = Field(
        default=None, description="JQL query for fetching operational tickets"
    )
    pagination_max_results: int = Field(
        default=200, description="Maximum results per page for pagination"
    )
    lookback_days: int = Field(
        default=30, description="Number of days to look back for issues"
    )
    llm_description_prompt: str | None = Field(
        default=None, description="LLM prompt for generating issue descriptions"
    )
    llm_comments_prompt: str | None = Field(
        default=None, description="LLM prompt for generating issue comments"
    )
    client_timeout: float = Field(
        default=60.0, description="HTTP client timeout in seconds for Jira API requests"
    )
    llm_concurrency: int = Field(
        default=10, description="Number of concurrent LLM calls during issue enrichment"
    )
    catch_up_min_gap_minutes: int = Field(
        default=10,
        description="Minimum gap in minutes to trigger a catch-up batch instead of switching to lookback",
    )
    sqs_queue_url: str | None = Field(
        default=None,
        description="Scribe historian SQS queue URL for Jira issue writes",
    )
    sqs_queue_region: str = Field(
        default="us-east-1",
        description="AWS region for the Scribe historian SQS queue",
    )
