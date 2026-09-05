"""Operation-level span wrapping an LLM call.

Wraps an LLM call in a span named after the operation, so a trace is identifiable
in Braintrust (e.g. "root_cause_summary" / "incident_correlation") instead of a
bare "openai.chat" / "bedrock.chat". Provider-agnostic: the wrapped call can be a
Facade (auto-instrumented) or Bedrock (manual) span, which nests underneath this
one. Any service can use it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

__all__ = ["traced_llm_operation"]

_tracer = trace.get_tracer(__name__)


@contextmanager
def traced_llm_operation(
    operation: str,
    source: str | None = None,
    entity_id: dict[str, Any] | None = None,
    entity_created_at: datetime | None = None,
) -> Iterator[Span]:
    """Open a span named ``operation`` around an LLM call.

    Records ``operation`` (and ``source`` when given) as span attributes — which
    Braintrust surfaces as filterable metadata — and as ``braintrust.tags`` for
    the Tags column. Tags union at the trace level with the resource-level
    environment/service tags, so a trace ends up carrying all of them.

    ``entity_id`` carries the business identifier the call is for (e.g.
    ``{"incident_id": "INC-123"}``, ``{"pr_number": "456"}``,
    ``{"issue_key": "TCMR-1"}``) so a trace in Braintrust is identifiable
    without opening it. Each key/value becomes its own span attribute and tag,
    since the key names vary by source type (incident, PR, TCMR, ...).

    ``entity_created_at`` records when the entity itself was opened/created (e.g.
    an incident's creation time), as a span attribute only — never a tag, since a
    per-trace timestamp would just add noise to Braintrust's Tags column. Comparing
    it against the span's own ``created`` timestamp in a BTQL query is how you find,
    e.g., traces produced within the first N minutes of an incident's life
    (`to_datetime(created) - to_datetime(metadata.entity_created_at) < interval N minute`).

    A cheap no-op when tracing is disabled (the default tracer isn't recording).

    Args:
        operation: Operation label (e.g. "root_cause_summary",
            "incident_correlation") — becomes the span name.
        source: Optional data source (e.g. "incidentio", "jira", "correlation").
        entity_id: Optional dict of the entity primary key this call is for
            (e.g. an EnrichmentRequest.entity_id).
        entity_created_at: Optional creation timestamp of the entity this call
            is for (e.g. the incident's own created_at).
    """
    tags = [f"operation:{operation}"]
    if source:
        tags.append(f"source:{source}")
    if entity_id:
        tags.extend(f"{key}:{value}" for key, value in entity_id.items())
    with _tracer.start_as_current_span(operation) as span:
        span.set_attribute("operation", operation)
        if source:
            span.set_attribute("source", source)
        if entity_id:
            for key, value in entity_id.items():
                span.set_attribute(key, str(value))
        if entity_created_at:
            span.set_attribute("entity_created_at", entity_created_at.isoformat())
        span.set_attribute("braintrust.tags", tags)
        yield span
