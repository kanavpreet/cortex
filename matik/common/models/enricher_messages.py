"""Enricher service message models."""

from datetime import datetime
from typing import Any, Literal

from sqlmodel import Field, SQLModel


class EnrichmentRequest(SQLModel):
    """Message consumed from the enricher SQS queue.

    Produced by the Historian or Chronicler when a new entity is ready for
    LLM enrichment. The Enricher uses this message to look up the appropriate
    prompt mapping, compute content hashes, call Facade LLM for changed fields,
    and publish results to the Scribe queue.
    """

    source_type: str = Field(
        ...,
        description=(
            "Data source identifier (e.g., 'incidentio', 'ghe_pr', 'jira'). "
            "Must match a key in enricher config source_mappings."
        ),
    )
    producer: Literal["historian", "chronicler"] = Field(
        ...,
        description="Service that produced this enrichment request.",
    )
    task_id: str = Field(
        ...,
        description="Unique identifier for this enrichment task, used for log correlation.",
    )
    entity_id: dict[str, Any] = Field(
        ...,
        description=(
            "Primary key fields for the entity being enriched "
            "(e.g., {'incident_id': '123'}). Forwarded verbatim to the Scribe message."
        ),
    )
    content: dict[str, Any] = Field(
        ...,
        description=(
            "Raw content fields available for enrichment "
            "(e.g., {'summary': '...', 'resolution_statement': '...'})."
        ),
    )
    entered_at: datetime | None = Field(
        default=None,
        description=(
            "DLQ retry staleness guard (ADR 024): the time this fact entered "
            "Matik, assigned once by the producer (Historian/Chronicler) and "
            "forwarded unchanged into the Enricher's outbound Scribe message "
            "— never re-stamped. None on requests that predate the guard."
        ),
    )
