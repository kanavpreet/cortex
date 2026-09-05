"""Generic sheet read-back, backfill detection, and write for any audit type.

The row key and the three per-audit-type callables come from the audit's
``AuditSpec`` — this module has no knowledge of what a specific audit type
(e.g. root cause coverage) actually does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from common.llm_tracing import traced_llm_operation
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Processing lifecycle, not conclusion -- a row's own ``audit_status`` values
# (what got concluded) are audit-type-specific and live with that audit type.
# This is the one generic signal every audit type shares: is this row still
# something a later run might need to revisit, or is it done for good.
ONGOING = "ongoing"
FINAL = "final"


@dataclass(frozen=True)
class AuditSpec:
    """Declarative description of one continuous production audit."""

    row_key: str
    """Column name used as the unique row key in the sheet (e.g. "reference_id")."""

    find_eligible_entities: Callable[[Any], list[dict[str, Any]]]
    """(config) -> entities eligible for this run. Each entity is a dict with
    at least ``row_key`` set."""

    process_entity: Callable[[Any, dict[str, Any]], dict[str, Any]]
    """(config, entity) -> a sheet row for a newly-seen entity. Must set
    ``overall_status`` to ``ONGOING`` or ``FINAL``."""

    rescore_entity: Callable[[Any, dict[str, Any]], dict[str, Any] | None]
    """(config, sheet_row) -> an updated row if an ``ongoing`` row's
    situation has changed since the last run, else None."""


class SheetsClientProtocol(Protocol):
    """Minimal interface ``sheet_sync`` needs from a sheets client."""

    def read_rows(self) -> list[dict[str, Any]]: ...

    def upsert_rows(self, rows: list[dict[str, Any]]) -> None: ...


@dataclass
class RunPlan:
    """What a single run needs to do, computed with no I/O."""

    to_process: list[dict[str, Any]] = field(default_factory=list)
    """Eligible entities not yet in the sheet at all — run the full pipeline."""

    to_rescore: list[dict[str, Any]] = field(default_factory=list)
    """Sheet rows still ongoing that may have moved forward since the last run."""


def compute_run_plan(
    entities: list[dict[str, Any]],
    sheet_rows: list[dict[str, Any]],
    row_key: str,
) -> RunPlan:
    """Decide what a run needs to do, given eligible entities and current sheet state.

    Pure logic, no I/O. ``final`` rows are always skipped — postmortem-time
    correlations are a one-time-only read by design, so a final row is never
    re-extracted or re-classified. ``ongoing`` rows are offered for rescoring
    on every run (cheap: the spec decides whether anything has actually
    changed). Entities with no existing row are processed fresh.
    """
    sheet_by_key = {row[row_key]: row for row in sheet_rows}

    to_process = [e for e in entities if e[row_key] not in sheet_by_key]
    to_rescore = [row for row in sheet_rows if row.get("overall_status") == ONGOING]

    return RunPlan(to_process=to_process, to_rescore=to_rescore)


def run_sheet_sync(
    spec: AuditSpec,
    config: Any,
    sheets_client: SheetsClientProtocol,
) -> None:
    """Execute one audit run end to end against the sheet.

    Finds eligible entities, reads current sheet state, computes the run plan,
    processes new entities and rescores ongoing ones via the spec's callables, then
    writes every changed row back in one batch. A single entity's failure is
    logged and skipped rather than failing the whole run.
    """
    with traced_llm_operation("audit_run", source=spec.row_key) as run_span:
        entities = spec.find_eligible_entities(config)
        sheet_rows = sheets_client.read_rows()
        plan = compute_run_plan(entities, sheet_rows, spec.row_key)

        updated_rows: list[dict[str, Any]] = []

        for entity in plan.to_process:
            try:
                with traced_llm_operation(
                    "audit_process_entity",
                    source=spec.row_key,
                    entity_id={spec.row_key: entity.get(spec.row_key)},
                ):
                    row = spec.process_entity(config, entity)
            except Exception:
                logger.exception(
                    "failed to process entity",
                    row_key=entity.get(spec.row_key),
                )
                continue
            updated_rows.append(row)

        for row in plan.to_rescore:
            try:
                with traced_llm_operation(
                    "audit_rescore_entity",
                    source=spec.row_key,
                    entity_id={spec.row_key: row.get(spec.row_key)},
                ):
                    rescored = spec.rescore_entity(config, row)
            except Exception:
                logger.exception(
                    "failed to rescore entity",
                    row_key=row.get(spec.row_key),
                )
                continue
            if rescored is not None:
                updated_rows.append(rescored)

        if updated_rows:
            sheets_client.upsert_rows(updated_rows)

        run_span.set_attribute("processed_count", len(plan.to_process))
        run_span.set_attribute("rescore_count", len(plan.to_rescore))
        run_span.set_attribute("rows_written", len(updated_rows))

        logger.info(
            "audit run complete",
            processed=len(plan.to_process),
            rescore_candidates=len(plan.to_rescore),
            rows_written=len(updated_rows),
        )
