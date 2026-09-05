"""DataSourceSpec registry — the single wiring point for a data source.

Each ingestion source (Incident.io, JIRA, GHE PR, ...) is declared once as a
``DataSourceSpec`` and registered here. Downstream infrastructure — the
model-derived ``BaseUpsertDAO``, the generic Scribe handler, and (later) the
historian runner — reads the spec instead of hand-wiring per-source column
lists, handler classes, and routing entries.

Spec modules self-register on import; ``common/datasources/__init__.py`` imports
them so importing the package populates the registry (the same pattern as
``chronicler/transformers/__init__.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel
    from sqlalchemy import Engine
    from sqlmodel import SQLModel

    from common.daos.base_dao import BaseUpsertDAO
    from common.metrics import DBMetrics


@dataclass(frozen=True)
class DataSourceSpec:
    """Declarative description of one ingestion source.

    Fields are a superset covering both the DAO write path (``record_model`` +
    ``conflict_keys`` + ``update_columns``) and Scribe routing (``dao_factory``,
    ``enrichment_message_model``, ``enrichment_key``, ``llm_columns``,
    ``enrichment_hook_target``). Not every consumer reads every field.
    """

    # --- identity ---
    source_type: str
    """Scribe/enricher discriminator, e.g. "incidentio"."""

    # --- persistence (drives BaseUpsertDAO) ---
    record_model: type[SQLModel]
    """The ``table=True`` SQLModel; ``__table__`` drives the derived columns."""
    conflict_keys: list[str]
    """Unique-index columns backing ON DUPLICATE KEY UPDATE (never updated)."""
    exclude_columns: list[str] = field(default_factory=list)
    """Extra columns to keep OUT of the derived ``update_columns`` — the denylist
    knob for per-source policy exclusions (e.g. immutable creation timestamps like
    ``created_at``). Conflict keys, LLM columns, and DB-managed audit columns
    (``id``/``row_created_at``/``row_updated_at``) are excluded automatically and
    need not be listed here."""

    # --- Scribe routing / handler ---
    dao_factory: Callable[[Engine, DBMetrics | None], BaseUpsertDAO] | None = None
    """Builds the source's DAO from (engine, metrics)."""
    base_message_model: type[BaseModel] | None = None
    """Pydantic model a partial-update base message validates into. Only set
    for sources whose base write patches a subset of columns on an existing
    row rather than upserting a full record. ``None`` (the default, and
    correct for enrichment-only sources with no base write at all — e.g.
    "incident_channel_summary") means the standard full-record
    ``record_model`` + ``upsert_batch`` path is used."""
    base_partial_update: str | None = None
    """DAO method name invoked per-message when ``base_message_model`` is set.
    The method's kwargs are the validated base message's fields minus
    ``source_type``/``message_type``. Returns ``True`` on success, ``False``
    if the row isn't present yet (raises ``BaseRecordNotFoundError`` so
    Scribe retries), ``None`` on DB error."""
    enrichment_message_model: type[BaseModel] | None = None
    """Pydantic model the enrichment message validates into. ``None`` means the
    source has no enrichment route (base-only)."""
    enrichment_key: str | None = None
    """Identifier field on the enrichment message + record used to locate the
    row for ``update_llm_fields`` (e.g. "incident_id")."""
    enrichment_hook_target: Literal["message", "record"] = "message"
    """Whether the post-write enrichment hook receives the enrichment *message*
    (default, cheap) or the full re-fetched *record* (incidentio, whose
    enigmatologist correlation hook needs the incident)."""
    record_finder: str | None = None
    """DAO method name used to re-fetch the record when
    ``enrichment_hook_target == "record"`` (e.g. "find_incident_by_id")."""

    enrichment_overwrites_with_none: bool = False
    """Whether an enrichment update writes ``None`` LLM columns as SQL NULL.

    Default (``False``): ``None`` values are dropped so a missing field never
    overwrites an existing one (incidentio, jira). ``True`` preserves GHE PR's
    historical "always write both LLM columns" semantics, where a ``None`` blanks
    the column rather than leaving it untouched. Only differs in the degenerate
    all-``None`` enrichment case — the Enricher always supplies real values."""

    base_column_flags: dict[str, str] = field(default_factory=dict)
    """Boolean base-message flags that gate a single column in the batch upsert.

    Maps a base-message field name -> the column it gates. When the flag is
    ``False`` on a base message, that column is dropped from the ON DUPLICATE KEY
    UPDATE set so existing values are preserved on re-write; when ``True`` (the
    default when the field is absent) the column is written normally. All messages
    in a flush batch must agree on each flag — a mixed batch is a routing bug and
    is rejected. Jira declares ``{"update_services": "services"}`` so a crawl that
    skipped service enrichment does not clobber previously-resolved services."""

    # --- DLQ retry staleness guard (ADR 024) ---
    base_entered_at_column: str | None = None
    """Column storing the base write-group's guard timestamp (e.g.
    ``incidentio_base_entered_at``). ``None`` (the default) means this
    write-group has no staleness guard yet — the base upsert stays
    unconditional. When set, ``BaseUpsertDAO._upsert_chunk`` only applies an
    incoming base message's columns when its ``entered_at`` is newer than (or
    the stored guard timestamp is NULL)."""
    enrichment_entered_at_column: str | None = None
    """Column storing the enrichment write-group's guard timestamp (e.g.
    ``incidentio_enrichment_entered_at``). Same semantics as
    ``base_entered_at_column``, applied by
    ``BaseUpsertDAO.update_llm_fields_from_message``."""

    # Columns the DB owns; never written by a base upsert regardless of source.
    _DB_MANAGED_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {"id", "row_created_at", "row_updated_at"}
    )

    @property
    def llm_columns(self) -> list[str]:
        """Enrichment-only columns, derived from ``enrichment_message_model``.

        These are the message's fields minus the routing discriminators
        (``source_type``/``message_type``), the ``enrichment_key`` identifier,
        and ``entered_at`` (staleness-guard metadata, not an LLM/hash column —
        it is written to ``enrichment_entered_at_column`` separately, see ADR
        024) — i.e. exactly the LLM/hash columns ``update_llm_fields`` writes.
        Empty when the source has no enrichment route.
        """
        if self.enrichment_message_model is None:
            return []
        skip = {"source_type", "message_type", "entered_at"}
        if self.enrichment_key is not None:
            skip.add(self.enrichment_key)
        return [
            name
            for name in self.enrichment_message_model.model_fields
            if name not in skip
        ]

    @property
    def update_columns(self) -> list[str]:
        """Columns a base upsert overwrites on conflict, derived from the model.

        Every table column is updatable by default EXCEPT: the conflict keys, the
        DB-managed audit columns, the LLM columns (so a base write never clobbers
        async enrichment), and anything in ``exclude_columns`` (the per-source
        denylist). To keep a new column out of base writes, add it to
        ``exclude_columns`` — the derivation does the rest.
        """
        excluded = (
            set(self.conflict_keys)
            | self._DB_MANAGED_COLUMNS
            | set(self.llm_columns)
            | set(self.exclude_columns)
        )
        return [
            c.name
            for c in self.record_model.__table__.columns  # type: ignore[attr-defined]
            if c.name not in excluded
        ]


_REGISTRY: dict[str, DataSourceSpec] = {}


def register_source(spec: DataSourceSpec) -> None:
    """Register a data source spec by its ``source_type``."""
    _REGISTRY[spec.source_type] = spec


def get_source(source_type: str) -> DataSourceSpec:
    """Look up a registered spec.

    Raises:
        KeyError: if no spec is registered for ``source_type``.
    """
    return _REGISTRY[source_type]


def all_sources() -> list[DataSourceSpec]:
    """Return all registered specs."""
    return list(_REGISTRY.values())
