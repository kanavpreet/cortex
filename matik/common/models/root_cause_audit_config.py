"""Root-cause-coverage audit configuration."""

from sqlmodel import Field, SQLModel


class RootCauseAuditConfig(SQLModel):
    """Configuration for the root-cause-coverage audit.

    Note: No table=True, this is a configuration model only.
    """

    lookback_hours: int = Field(
        default=24,
        description="How far back to look for newly-terminal incidents on each run",
    )
    investigation_window_minutes: int = Field(
        default=20,
        description=(
            "Max minutes after an incident's created_at for a Braintrust "
            "correlation rerun to still count as investigation-time"
        ),
    )
    llm_provider: str = Field(
        default="facade",
        description="LLM provider for ground-truth extraction: 'facade' or 'bedrock'",
    )
    braintrust_trace_environment: str = Field(
        default="production",
        description=(
            "deployment.environment to filter for when reading correlation "
            "traces back from Braintrust (see BraintrustClient.trace_environment) "
            "-- the audit's target environment, independent of which "
            "environment the audit job itself runs in"
        ),
    )
    min_llm_score: float = Field(
        default=0.3,
        description=(
            "Must mirror EnigmatologistConfig.min_llm_score -- production's "
            "assign_correlations_by_llm node drops candidates below this score "
            "before persisting them, so BraintrustClient re-applies the same "
            "threshold when reading raw traces back, or investigation-time "
            "correlations would include candidates the LLM rejected"
        ),
    )
    speculative_patterns: list[str] = Field(
        default_factory=list,
        description="Must mirror EnigmatologistConfig.speculative_patterns",
    )
    speculative_max_score: float = Field(
        default=0.7,
        description="Must mirror EnigmatologistConfig.speculative_max_score",
    )
    correlations_worksheet_name: str = Field(
        default="correlations",
        description=(
            "Worksheet (tab), in the same spreadsheet as sheets.worksheet_name, "
            "that gets one appended row per classified correlation -- keeps "
            "correlations pivot/dashboard-friendly instead of packed into a "
            "JSON cell on the main incidents row"
        ),
    )
    ground_truth_extraction_prompt: str = Field(
        default="",
        description="System prompt for the ground-truth extraction LLM call",
    )
