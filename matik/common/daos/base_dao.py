"""Base DAO providing generic batch-upsert and partial-update helpers.

Concrete DAOs subclass ``BaseUpsertDAO`` to avoid re-implementing the multi-row
``INSERT ... ON DUPLICATE KEY UPDATE`` and the LLM partial-update pattern that
was previously hand-written (and copy-pasted) in each source's DAO.

The batch upsert uses SQLAlchemy's native MySQL construct
(``sqlalchemy.dialects.mysql.insert(...).on_duplicate_key_update(...)``) instead
of a hand-built ``text()`` statement. All writes route through
``execute_with_retry`` so transient MySQL failures are retried on a fresh
connection, and every query is wrapped in the standard ``DBMetrics.start_query``
timing idiom.

Construction is spec-driven: a DAO passes its ``DataSourceSpec`` and the insert
row is derived generically from ``spec.record_model`` (``model_dump``), while
``upsert_batch`` / ``update_llm_fields_from_message`` read ``spec.update_columns``
/ ``spec.llm_columns``. There are no hand-maintained per-source column lists or
row builders. List/JSON columns are passed to SQLAlchemy as native Python objects
so the ``JSON`` column type serializes them exactly once (no manual pre-encoding).
"""

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Column, Engine, case, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from common.datasources.registry import DataSourceSpec
from common.utils import log_utils
from common.utils.retry_utils import execute_with_retry, fetch_all_with_retry

if TYPE_CHECKING:
    from common.metrics import DBMetrics

logger = log_utils.get_logger(__name__)

# MySQL packet size safe limit for multi-row statements.
BATCH_SIZE = 500

# Columns never written by the derived-row upsert path: the autoincrement PK and
# the DB-managed audit columns (server_default / ON UPDATE CURRENT_TIMESTAMP).
_DB_MANAGED_COLUMNS = frozenset({"id", "row_created_at", "row_updated_at"})


class WriteOutcome(Enum):
    """Outcome of a guarded partial update (see ADR 024-dlq-retry-staleness-guard.md).

    A plain ``UPDATE ... WHERE pk = :id`` can't tell "row absent" apart from
    "row present but the write lost the staleness guard" — both produce
    ``rowcount == 0``. Conflating the two would make a legitimately fresher
    retry loop forever (misread as "base not found") instead of being acked.
    ``_update_partial`` disambiguates via a follow-up existence probe.
    """

    WRITTEN = "written"
    """The update applied (or there was nothing to write)."""
    SKIPPED_STALE = "skipped_stale"
    """The row exists but the incoming ``entered_at`` lost the guard — a
    no-op success, not a retry trigger."""
    BASE_NOT_FOUND = "base_not_found"
    """No row matched the key at all."""
    ERROR = "error"
    """A database error occurred."""


class BaseUpsertDAO:
    """Shared batch-upsert and partial-update behavior for source DAOs.

    Spec-driven: pass a ``DataSourceSpec`` and the ``Engine``. The insert row is
    derived generically from ``spec.record_model`` (``model_dump`` + reflected
    JSON columns), and ``upsert_batch`` / ``update_llm_fields_from_message`` read
    ``spec.update_columns`` / ``spec.llm_columns`` — there are no per-source
    column lists in the DAO.
    """

    def __init__(
        self,
        spec: DataSourceSpec,
        engine: Engine,
        metrics: "DBMetrics | None" = None,
    ) -> None:
        """Initialize the DAO from its data source spec.

        Args:
            spec: The source's ``DataSourceSpec`` (drives the derived row and the
                update/LLM column contract).
            engine: SQLAlchemy engine for database operations.
            metrics: Optional ``DBMetrics`` for query instrumentation.
        """
        self._spec = spec
        self._engine = engine
        self._metrics = metrics
        self._table = spec.record_model.__table__  # type: ignore[attr-defined]

    def _derived_row(self, model: Any) -> dict[str, Any]:
        """Build the column→value row generically from a validated model.

        The model's field validators already normalize values (e.g. timestamps
        to naive UTC), so ``model_dump`` yields write-ready values passed straight
        through to SQLAlchemy. JSON/list columns are handed over as native Python
        objects (e.g. ``list[str]``) — SQLAlchemy's ``JSON`` column type serializes
        them exactly once on write. (Do NOT ``json.dumps`` here: the value would be
        encoded twice and stored as a JSON string like ``'["foo"]'`` instead of a
        real array.) DB-managed columns (``id`` + audit columns) are excluded so
        they are never written.
        """
        data = model.model_dump()
        return {
            col.name: data[col.name]
            for col in self._table.columns
            if col.name not in _DB_MANAGED_COLUMNS and col.name in data
        }

    def upsert_batch(
        self, models: Sequence[Any], dropped_columns: Sequence[str] = ()
    ) -> int | None:
        """Spec-driven batch upsert.

        Derives each row from the model and updates ``spec.update_columns`` on
        conflict. ``dropped_columns`` are removed from that update set so their
        existing values are preserved on re-write — used for spec base-column
        flags (e.g. jira ``update_services=False`` drops ``services``). Returns
        total affected rows, ``0`` for empty input, ``None`` if any chunk fails.
        """
        update_columns = self._spec.update_columns
        if dropped_columns:
            dropped = set(dropped_columns)
            update_columns = [c for c in update_columns if c not in dropped]
        return self._batch_upsert(models, update_columns)

    def update_llm_fields_from_message(
        self, entered_at: datetime | None = None, **values: Any
    ) -> WriteOutcome:
        """Spec-driven LLM partial update.

        ``values`` are the enrichment message's llm fields (already keyed by
        column name); the identifier column is ``spec.enrichment_key`` and its
        value is popped from ``values``. By default ``None`` values are dropped so
        a missing field never overwrites an existing one; sources that set
        ``enrichment_overwrites_with_none`` write them as SQL NULL instead (GHE
        PR's historical "always write both LLM columns" behavior).

        ``entered_at`` is the message's staleness-guard timestamp (see ADR
        024-dlq-retry-staleness-guard.md), separate from ``values`` since it
        maps to ``spec.enrichment_entered_at_column``, not a literal message
        field name. ``None`` (the default — callers with no message envelope,
        e.g. ``update_llm_fields`` convenience wrappers) or a spec with no
        ``enrichment_entered_at_column`` falls back to an unconditional write.
        """
        key_col_name = self._spec.enrichment_key
        if key_col_name is None:
            raise RuntimeError("spec has no enrichment_key")
        key_value = values.pop(key_col_name)
        if self._spec.enrichment_overwrites_with_none:
            set_values = values
        else:
            set_values = {k: v for k, v in values.items() if v is not None}
        return self._update_partial(
            self._table.c[key_col_name],
            key_value,
            set_values,
            entered_at_column=self._spec.enrichment_entered_at_column,
            entered_at=entered_at,
        )

    def _batch_upsert(
        self,
        rows: Sequence[Any],
        upsert_columns: Sequence[str],
    ) -> int | None:
        """Batch upsert ``rows`` via INSERT ... ON DUPLICATE KEY UPDATE.

        Chunks by ``BATCH_SIZE`` to stay under the MySQL packet limit and
        aggregates affected-row counts. Returns ``None`` if any chunk fails so
        callers can treat it as a write failure.

        Args:
            rows: Domain models to upsert (each mapped to values via
                ``_derived_row``).
            upsert_columns: Column names updated in the ON DUPLICATE KEY UPDATE
                clause (excludes the unique key, immutable, and LLM columns).

        Returns:
            Total affected rows on success, ``0`` for empty input, ``None`` if any
            chunk fails.
        """
        if not rows:
            return 0

        table = self._table
        total_affected = 0
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i : i + BATCH_SIZE]
            affected = self._upsert_chunk(chunk, upsert_columns)
            if affected is None:
                logger.error(
                    "batch upsert failed", table=table.name, chunk_start_index=i
                )
                return None
            total_affected += affected

        logger.info(
            "upserted rows",
            table=table.name,
            count=len(rows),
            affected_rows=total_affected,
        )
        return total_affected

    def _upsert_chunk(
        self,
        rows: Sequence[Any],
        upsert_columns: Sequence[str],
    ) -> int | None:
        """Upsert a single chunk (<= BATCH_SIZE) using native mysql insert.

        Returns:
            Affected row count on success, ``0`` for empty input, ``None`` on error.
        """
        if not rows:
            return 0

        table = self._table
        record = (
            self._metrics.start_query("upsert", table.name) if self._metrics else None
        )
        try:
            values = [self._derived_row(row) for row in rows]
            stmt = mysql_insert(table).values(values)

            ts_col_name = self._spec.base_entered_at_column
            if ts_col_name is not None:
                # Staleness guard (ADR 024): only take the incoming row's
                # write-group columns when its entered_at is newer than the
                # stored guard timestamp, the stored one is NULL (first
                # write), or the incoming one is NULL (message predates the
                # guard — falls back to unconditional, per decision #5).
                # MySQL evaluates VALUES()/stmt.inserted per conflicting row,
                # so this is correct for a multi-row INSERT ... ON DUPLICATE
                # KEY UPDATE, and it's a single atomic statement — no
                # read-compare-write window for a concurrent writer to race.
                ts_col = table.c[ts_col_name]
                incoming_ts = stmt.inserted[ts_col_name]
                fresher = or_(
                    incoming_ts.is_(None), ts_col.is_(None), incoming_ts > ts_col
                )
                # The guard column itself gets narrower treatment than the
                # payload columns it protects: a NULL incoming entered_at
                # lets payload columns win unconditionally (backward compat),
                # but must NOT regress the stored guard back to NULL — that
                # would un-protect the row for the next write, letting a
                # genuinely stale redrive with a real (old) entered_at land
                # against a NULL guard and win. The guard only ever advances,
                # never resets, and only on a real, fresher incoming value.
                guard_advances = incoming_ts.isnot(None) & or_(
                    ts_col.is_(None), incoming_ts > ts_col
                )
                update_map: dict[str, Any] = {
                    col: (
                        case((guard_advances, incoming_ts), else_=ts_col)
                        if col == ts_col_name
                        else case((fresher, stmt.inserted[col]), else_=table.c[col])
                    )
                    for col in upsert_columns
                }
            else:
                update_map = {col: stmt.inserted[col] for col in upsert_columns}
            stmt = stmt.on_duplicate_key_update(update_map)

            result = execute_with_retry(self._engine, stmt, op=f"upsert:{table.name}")

            if record:
                record(None)
            return result.rowcount
        except Exception as e:
            logger.exception("error in batch upsert", table=table.name, error=str(e))
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return None

    def find_stale_base_keys(self, records: Sequence[Any]) -> set[Any]:
        """Identify which just-upserted ``records`` lost the base staleness guard.

        ``_upsert_chunk``'s ``INSERT ... ON DUPLICATE KEY UPDATE`` only reports
        an aggregate affected-row count for the whole chunk (see
        ``_batch_upsert``) — it can't say which individual rows actually took
        the incoming values versus keeping the stored ones under ADR 024's
        per-column ``case()`` guard. Callers that run a per-record post-write
        hook after ``upsert_batch`` (e.g. Scribe's ``handle_base_batch``, whose
        ``incidentio`` hooks trigger enigmatologist correlation) need to know
        this so a stale, guard-rejected record doesn't fire a hook as if its
        write had won.

        Re-reads ``spec.base_entered_at_column`` for the batch's keys (keyed by
        ``spec.enrichment_key``) and flags a record as stale iff the stored
        value is strictly newer than what that record tried to write — i.e.
        some other write already won.

        This is a follow-up read, not part of the upsert's own transaction, so
        it carries the same narrow race window as ``_update_partial``'s
        disambiguation: a same-key write landing in the gap between the upsert
        and this read could in principle flip a verdict. It is a best-effort
        signal for skipping a hook, not a correctness-critical write path.

        Returns an empty set (no extra query) when the spec has no
        ``base_entered_at_column``/``enrichment_key``, or when no record in
        the batch carries a guard timestamp — the common, unguarded case.
        """
        ts_col_name = self._spec.base_entered_at_column
        key_col_name = self._spec.enrichment_key
        if ts_col_name is None or key_col_name is None:
            return set()

        attempted: dict[Any, datetime] = {}
        for record in records:
            entered_at = getattr(record, ts_col_name, None)
            if entered_at is None:
                continue
            key_value = getattr(record, key_col_name, None)
            if key_value is not None:
                attempted[key_value] = entered_at

        if not attempted:
            return set()

        table = self._table
        key_col = table.c[key_col_name]
        ts_col = table.c[ts_col_name]
        probe = select(key_col, ts_col).where(key_col.in_(attempted.keys()))
        rows = fetch_all_with_retry(
            self._engine, probe, op=f"stale_base_probe:{table.name}"
        )

        return {
            key_value
            for key_value, stored_ts in rows
            if stored_ts is not None and stored_ts > attempted[key_value]
        }

    def _update_partial(
        self,
        key_column: Column[Any],
        key_value: Any,
        values: dict[str, Any],
        entered_at_column: str | None = None,
        entered_at: datetime | None = None,
    ) -> WriteOutcome:
        """Apply a partial UPDATE of ``values`` to the row matched by the key.

        Shared implementation for the ``update_llm_fields`` pattern: only the
        supplied columns are written (base fields are never touched), identified
        by ``key_column == key_value``.

        When ``entered_at_column`` and ``entered_at`` are both set (staleness
        guard, ADR 024), the freshness predicate is folded into the same
        ``WHERE`` clause as the key match — one atomic statement, no
        read-compare-write race — and ``entered_at`` is written alongside
        ``values``. A NULL stored guard timestamp, or a missing ``entered_at``
        on this call, always allows the write (decision #5: never skip on
        missing data). A 0-rowcount guarded UPDATE is disambiguated by
        ``_resolve_guarded_zero_rowcount``, which also closes a narrow race
        where the row is inserted by a concurrent base write in the gap
        between this UPDATE and the disambiguation read.

        Args:
            key_column: The column used to locate the row (e.g. ``incident_id``).
            key_value: The key value to match.
            values: Column→value pairs to set. Callers should already have
                dropped ``None`` values they don't want to overwrite; an empty
                dict short-circuits to success without a DB call.
            entered_at_column: The write-group's guard timestamp column, or
                ``None`` if this write-group has no guard yet.
            entered_at: The incoming message's guard timestamp, or ``None``.

        Returns:
            ``WriteOutcome.WRITTEN`` on success (>=1 row updated, or nothing to
            update); ``WriteOutcome.SKIPPED_STALE`` if the row exists but lost
            the freshness guard; ``WriteOutcome.BASE_NOT_FOUND`` if no row
            matched the key at all; ``WriteOutcome.ERROR`` on database error.
        """
        if not values:
            return WriteOutcome.WRITTEN

        table = self._table
        record = (
            self._metrics.start_query("update", table.name) if self._metrics else None
        )
        guarded = False
        ts_col: Column[Any] | None = None
        try:
            where_clause = key_column == key_value
            write_values = values
            if entered_at_column is not None and entered_at is not None:
                guarded = True
                ts_col = table.c[entered_at_column]
                where_clause = where_clause & or_(ts_col.is_(None), ts_col < entered_at)
                write_values = {**values, entered_at_column: entered_at}

            stmt = table.update().where(where_clause).values(**write_values)
            result = execute_with_retry(
                self._engine, stmt, op=f"update_partial:{table.name}"
            )

            if result.rowcount == 0:
                if guarded:
                    assert ts_col is not None
                    outcome = self._resolve_guarded_zero_rowcount(
                        table, key_column, key_value, ts_col, entered_at, stmt
                    )
                    if record:
                        record(None)
                    return outcome

                logger.warning(
                    "partial update matched 0 rows — base record not yet present",
                    table=table.name,
                    key=str(key_value),
                )
                if record:
                    record(None)
                return WriteOutcome.BASE_NOT_FOUND

            if record:
                record(None)
            return WriteOutcome.WRITTEN
        except Exception as e:
            logger.exception(
                "error in partial update",
                table=table.name,
                key=str(key_value),
                error=str(e),
            )
            if record:
                try:
                    record(e)
                except Exception:
                    logger.warning("Failed to record metrics")
            return WriteOutcome.ERROR

    def _resolve_guarded_zero_rowcount(
        self,
        table: Any,
        key_column: Column[Any],
        key_value: Any,
        ts_col: Column[Any],
        entered_at: datetime | None,
        stmt: Any,
    ) -> WriteOutcome:
        """Disambiguate a 0-rowcount guarded UPDATE: row absent vs. guard-rejected.

        A plain UPDATE can't tell "no row matched the key" apart from "the row
        matched the key but lost the freshness guard" — both produce
        ``rowcount == 0``. Probes the guard column's *current value* directly
        (not mere existence): if the row is absent, that's a genuine
        ``BASE_NOT_FOUND``; if it exists and its stored guard value would still
        beat ``entered_at``, that's a genuine ``SKIPPED_STALE``.

        If the row exists but its stored guard value would NOT have blocked
        the write (NULL, or older than ``entered_at``), the original UPDATE's
        0-rowcount was an artifact of the row not existing yet *at the time
        that UPDATE ran* — it was inserted by a concurrent base write in the
        gap between that UPDATE and this probe. Misreading that as
        ``SKIPPED_STALE`` would silently drop a legitimate update instead of
        retrying it. Retrying the same guarded UPDATE once (now that the row
        exists) closes that window; if it still loses, some other write won
        in the interim and it's genuinely stale.
        """
        probe = select(ts_col).where(key_column == key_value).limit(1)
        rows = fetch_all_with_retry(
            self._engine, probe, op=f"update_partial_probe:{table.name}"
        )
        if not rows:
            return WriteOutcome.BASE_NOT_FOUND

        stored_ts = rows[0][0]
        if stored_ts is not None and entered_at is not None and stored_ts >= entered_at:
            logger.info(
                "partial update skipped — stale entered_at",
                table=table.name,
                key=str(key_value),
            )
            return WriteOutcome.SKIPPED_STALE

        # The guard would not have blocked us — the row simply wasn't there
        # yet when the first UPDATE ran. Retry now that it exists.
        retry_result = execute_with_retry(
            self._engine, stmt, op=f"update_partial_retry:{table.name}"
        )
        if retry_result.rowcount > 0:
            return WriteOutcome.WRITTEN

        # Someone else's fresher write landed in the retry's own gap too —
        # genuinely stale now rather than an infinite retry loop.
        logger.info(
            "partial update skipped — stale entered_at (after retry)",
            table=table.name,
            key=str(key_value),
        )
        return WriteOutcome.SKIPPED_STALE
