"""Biztech GitHub Enterprise configuration."""

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class BiztechGitHubConfig(SQLModel):
    """Biztech GitHub Enterprise configuration.

    Note: No table=True, this is a configuration model only.
    """

    ghe_app_private_key: str | None = Field(
        default=None, description="GitHub App private key content"
    )
    ghe_app_private_key_file_name: str | None = Field(
        default=None, description="GitHub App private key file name"
    )
    ghe_base_url: str = Field(
        ...,
        description="GitHub Enterprise base URL (host only, e.g. https://github.airbnb.biz)",
    )
    ghe_api_path: str = Field(
        default="/api/v3", description="GitHub Enterprise REST API path"
    )
    ghe_upload_path: str = Field(
        default="/api/uploads", description="GitHub Enterprise upload API path"
    )
    ghe_app_client_id: str = Field(..., description="GitHub App client ID")
    ghe_app_installation_id: str = Field(..., description="GitHub App installation ID")
    ghe_app_id: str = Field(..., description="GitHub App ID")
    ghe_pr_summary_prompt: str | None = Field(
        default=None, description="LLM prompt for generating PR summaries"
    )
    facade_model: str = Field(
        default="gpt-4", description="LLM model to use for Facade calls"
    )
    organizations: list[str] = Field(
        default_factory=lambda: ["Airbnb-ITX"],
        description="List of GitHub organizations to crawl",
    )
    page_size: int = Field(default=100, description="Pagination page size")
    cutoff_date: str | None = Field(
        default=None, description="Cutoff date for historical data fetching"
    )
    tracker_lookback_days: int | None = Field(
        default=None, description="Number of days to look back for tracker"
    )
    max_concurrent_repos: int | None = Field(
        default=None, description="Max concurrent repository processing"
    )
    repo_activity_buffer_minutes: int | None = Field(
        default=None,
        description=(
            "Minutes to subtract from the per-org last-crawl watermark when "
            "deciding which repos to re-scan, to absorb clock skew between the "
            "crawler and GitHub. Defaults to 15."
        ),
    )
    ingestible_comment_bots: list[str] = Field(
        default_factory=lambda: ["jenkins-prod", "spacelift-prod"],
        description=(
            "Base logins (no '[bot]' suffix) of the only bots whose PR conversation "
            "comments are folded into the summary. Accepts a comma-separated string."
        ),
    )
    jenkins_review_summary_title: str = Field(
        default="Review Summary",
        description=(
            "Substring matched (case-insensitively) against a jenkins-prod comment's "
            "first line to keep only the AirChat 'Review Summary'."
        ),
    )
    sqs_queue_url: str | None = Field(
        default=None,
        description="Scribe historian SQS queue URL for PR and tracker writes",
    )
    sqs_queue_region: str = Field(
        default="us-east-1", description="AWS region for the Scribe historian SQS queue"
    )

    @property
    def ghe_api_url(self) -> str:
        """Full GitHub Enterprise REST API URL (base_url + api_path)."""
        return f"{self.ghe_base_url.rstrip('/')}{self.ghe_api_path}"

    @property
    def ghe_upload_url(self) -> str:
        """Full GitHub Enterprise upload URL (base_url + upload_path)."""
        return f"{self.ghe_base_url.rstrip('/')}{self.ghe_upload_path}"

    @field_validator(
        "ghe_app_installation_id", "ghe_app_id", "ghe_app_client_id", mode="before"
    )
    @classmethod
    def coerce_to_string(cls, v: str | int | None) -> str | None:
        """Coerce int to string (YAML may parse unquoted numbers as int)."""
        if v is None:
            return None
        return str(v)

    @field_validator("ingestible_comment_bots", mode="before")
    @classmethod
    def split_csv(cls, v: str | list[str]) -> list[str]:
        """Allow a comma-separated string (kube-gen templates only scalars)."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
