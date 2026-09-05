"""Enricher service configuration models."""

from sqlmodel import Field, SQLModel


class SourceMappingEntry(SQLModel):
    """A single enrichment mapping for a given source type.

    Defines which content keys to combine as LLM input, which prompt to use
    (injected into the general prompt template), and what output field name to
    produce in the Scribe message.
    """

    input_keys: list[str] = Field(
        ...,
        description=(
            "Content keys from EnrichmentRequest.content to combine as LLM input. "
            "Multiple keys are concatenated with labeled sections."
        ),
    )
    output_field: str = Field(
        ...,
        description="Field name for the LLM output in the outbound Scribe message.",
    )
    prompt: str = Field(
        ...,
        description=(
            "Source-specific prompt instructions substituted into the general_prompt "
            "template via the {source_instructions} placeholder."
        ),
    )
    hash_field: str = Field(
        ...,
        description=(
            "DB column name for the combined content hash of all input_keys. "
            "The Enricher concatenates all input values with '||' and stores a "
            "single hash in this column to detect changes and skip unnecessary LLM calls."
        ),
    )


class EnricherConfig(SQLModel):
    """Configuration for the Enricher service.

    Loaded from kubegen YAML config. Controls SQS queue URLs, concurrency
    limits, and the config-driven source_mappings that determine which LLM
    enrichments to perform for each source type.
    """

    # SQS queue configuration
    enricher_queue_url: str = Field(
        ..., description="SQS URL for the enricher input queue."
    )
    enricher_dlq_url: str = Field(
        default="",
        description=(
            "SQS URL for the enricher dead-letter queue. "
            "Required for the enricher service; unused by historian publishers."
        ),
    )
    scribe_llm_queue_url: str = Field(
        default="",
        description=(
            "SQS URL for the Scribe LLM output queue. "
            "Required for the enricher service; unused by historian publishers."
        ),
    )
    sqs_queue_region: str | None = Field(
        default=None,
        description="AWS region for SQS queues. Defaults to us-east-1 if not set.",
    )
    sqs_max_messages: int = Field(
        default=10,
        description="Maximum number of messages to retrieve per SQS receive call.",
    )
    sqs_wait_time_seconds: int = Field(
        default=20,
        description="Long-poll wait time in seconds for SQS receive calls.",
    )
    sqs_poll_error_delay: int = Field(
        default=5,
        description="Seconds to sleep after an SQS poll error before retrying.",
    )

    # Concurrency
    max_concurrent_llm_calls: int = Field(
        default=20,
        description="Maximum number of concurrent Facade LLM calls.",
    )
    visibility_timeout_seconds: int = Field(
        default=300,
        description="SQS message visibility timeout in seconds while processing.",
    )
    max_receive_count: int = Field(
        default=3,
        description=(
            "SQS redrive policy maxReceiveCount for the enricher queue. Used to "
            "detect the final delivery attempt so a terminal DLQ log line and "
            "metric can be recorded before SQS silently redrives the message to "
            "the DLQ. Must match the maxReceiveCount on the queue's redrive policy."
        ),
    )

    # LLM prompt configuration
    general_prompt: str = Field(
        default="",
        description=(
            "General system prompt template with a {source_instructions} placeholder. "
            "Required for the enricher service; unused by historian publishers."
        ),
    )
    source_mappings: dict[str, list[SourceMappingEntry]] = Field(
        default_factory=dict,
        description=(
            "Per-source_type list of enrichment mappings. "
            "Required for the enricher service; unused by historian publishers."
        ),
    )

    hash_cache_ttl_seconds: int = Field(
        default=2592000,
        description="TTL in seconds for in-memory hash cache entries.",
    )
    hash_cache_max_size: int = Field(
        default=50000,
        description="Maximum number of entries in the in-memory hash cache.",
    )

    # LLM provider selection
    llm_provider: str = Field(
        default="facade",
        description="LLM provider to use for enrichment: 'facade' or 'bedrock'",
    )

    def build_prompt(self, source_instructions: str) -> str:
        """Build a complete system prompt by substituting source-specific instructions.

        Args:
            source_instructions: The source-specific prompt text from a SourceMappingEntry.

        Returns:
            The fully assembled system prompt with source instructions substituted in.
        """
        return self.general_prompt.format(source_instructions=source_instructions)

    @property
    def region(self) -> str:
        """Resolved AWS region, falling back to us-east-1."""
        return self.sqs_queue_region or "us-east-1"
