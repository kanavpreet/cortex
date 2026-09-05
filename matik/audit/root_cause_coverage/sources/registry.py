"""RootCauseSourceSpec registry — the single wiring point for a root-cause source.

Each source (GitHub PR, Jira TCMR, ...) is declared once as a
``RootCauseSourceSpec`` and registered here. ``verify_ground_truth`` loops
over every registered spec instead of branching on entity type by hand, and
``BraintrustClient`` is handed the correlation-engine field mapping derived
from this same registry instead of a hardcoded dict.

Spec modules self-register on import; ``audit/root_cause_coverage/sources/__init__.py``
imports them so importing the package populates the registry (the same
pattern as ``common/datasources/registry.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.models.root_cause_audit import VerifiedEntity

if TYPE_CHECKING:
    from audit.root_cause_coverage.pipeline import RootCauseAuditContext


@dataclass(frozen=True)
class RootCauseSourceSpec:
    """Declarative description of one root-cause source type."""

    entity_type: str
    """e.g. "github_pr", "jira_tcmr". Matches ``ReliabilityCorrelation.entity_type``."""

    parse: Callable[[str, str | None], Any]
    """(cited_identifier, quote_span) -> a parsed reference, or ``None`` if
    this source's identifier pattern doesn't match. The parsed reference's
    shape is source-specific -- it's only ever passed back into this same
    spec's ``verify``."""

    verify: Callable[[RootCauseAuditContext, Any], VerifiedEntity | None]
    """(pipeline context, parsed reference) -> a verified entity, or ``None``
    if it doesn't resolve against the real source. Must catch its own
    exceptions and return ``None`` on failure -- the registry loop doesn't
    wrap this call."""

    correlation_engine_field: str
    """The field name on enigmatologist's correlation-result model carrying
    this source's matches (e.g. "biztech_github") -- used to map Braintrust
    investigation-time correlations back to this audit's entity type."""

    build_links: Callable[[list[str], RootCauseAuditContext], dict[str, str]] | None = (
        None
    )
    """Optional: (entity_ids of this type, pipeline context) -> {entity_id:
    url}, for whichever of those ids resolve to a real link. Batched rather
    than one-at-a-time so a source needing a DB/API lookup (e.g. github_pr)
    does it once per run, not once per correlation row. Absent means this
    source has no linkable identifier -- the sheet row's url stays blank."""


_REGISTRY: dict[str, RootCauseSourceSpec] = {}


def register_root_cause_source(spec: RootCauseSourceSpec) -> None:
    """Register a root-cause source spec by its ``entity_type``."""
    _REGISTRY[spec.entity_type] = spec


def get_root_cause_source(entity_type: str) -> RootCauseSourceSpec | None:
    """Look up a registered spec by entity type, or ``None`` if unregistered."""
    return _REGISTRY.get(entity_type)


def all_root_cause_sources() -> list[RootCauseSourceSpec]:
    """Return all registered specs, in registration order."""
    return list(_REGISTRY.values())
