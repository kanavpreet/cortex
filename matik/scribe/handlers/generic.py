"""Registry-driven Scribe handler.

`GenericHandler` replaces the per-source base/enrichment handler classes. It is
parameterized by a `DataSourceSpec`, so adding a source's Scribe routing is a
spec registration rather than a new handler class. Behavior (validation, DAO
calls, return-contract handling, post-write hooks) matches the hand-written
per-source handlers it replaces.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from common.daos.base_dao import WriteOutcome
from common.utils import log_utils

from ..errors import BaseRecordNotFoundError, MalformedMessageError
from ._base import HandlerHooks

if TYPE_CHECKING:
    from common.daos.base_dao import BaseUpsertDAO
    from common.datasources.registry import DataSourceSpec

logger = log_utils.get_logger(__name__)


class GenericHandler:
    """Spec-driven handler for base events and enrichment updates."""

    def __init__(
        self,
        spec: "DataSourceSpec",
        dao: "BaseUpsertDAO",
        hooks: HandlerHooks | None = None,
    ) -> None:
        self._spec = spec
        self._dao = dao
        self._hooks = hooks if hooks is not None else HandlerHooks()

    async def handle_base_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Deserialize and batch-upsert a list of base records.

        Each message carries a ``data`` dict of the record model's fields.
        Sources that declare ``spec.base_column_flags`` may also carry top-level
        boolean flags (e.g. jira ``update_services``); a flag that is ``False``
        drops its gated column from the ON DUPLICATE KEY UPDATE set so existing
        values are preserved. All messages in a batch must agree on each flag.

        Sources that declare ``spec.base_partial_update`` instead never
        upsert a full record — see ``_handle_base_partial_batch``.

        Raises:
            MalformedMessageError: If any 'data' field is missing or fails
                validation, or messages disagree on a base-column flag.
            RuntimeError: If the DAO write returns None (DB error).
            BaseRecordNotFoundError: If a partial-update base message arrives
                before its target row exists.
        """
        if self._spec.base_partial_update is not None:
            await self._handle_base_partial_batch(parsed_list)
            return

        dropped_columns = self._resolve_dropped_columns(parsed_list)

        records: list[Any] = []
        for parsed in parsed_list:
            data = parsed.get("data")
            if not isinstance(data, dict):
                raise MalformedMessageError(
                    f"{self._spec.source_type} base message missing or invalid 'data'"
                )
            # Staleness guard (ADR 024): fold the message's entered_at into
            # the record under this write-group's guard column so the
            # derived row carries it straight into the upsert. A message
            # with no entered_at (predates the guard) leaves the column
            # unset — _upsert_chunk falls back to an unconditional write.
            ts_col = self._spec.base_entered_at_column
            entered_at = parsed.get("entered_at")
            if ts_col is not None and entered_at is not None:
                data = {**data, ts_col: entered_at}
            try:
                records.append(self._spec.record_model.model_validate(data))
            except ValidationError as e:
                raise MalformedMessageError(
                    f"{self._spec.record_model.__name__} validation failed: {e}"
                ) from e

        result = await asyncio.to_thread(
            self._dao.upsert_batch, records, dropped_columns
        )
        if result is None:
            raise RuntimeError(
                f"DB upsert failed for source_type={self._spec.source_type!r}"
            )

        logger.info(
            "scribe wrote base events",
            source_type=self._spec.source_type,
            count=len(records),
        )

        # Staleness guard (ADR 024): upsert_batch only returns an aggregate
        # affected-row count, not which records actually took their incoming
        # values versus losing the freshness race (ADR 024's per-column
        # case() guard in _upsert_chunk). Look up which keys lost before
        # running hooks, so a stale/redriven record (e.g. Incident.io, whose
        # base hook triggers enigmatologist correlation) can't fire a
        # downstream action off data the DB write just rejected. Skipped
        # entirely when this handler has no base hooks to run.
        stale_keys: set[Any] = set()
        key_col_name = self._spec.enrichment_key
        if self._hooks.base and key_col_name is not None:
            stale_keys = await asyncio.to_thread(
                self._dao.find_stale_base_keys, records
            )

        for record in records:
            if (
                key_col_name is not None
                and getattr(record, key_col_name, None) in stale_keys
            ):
                logger.info(
                    "scribe skipped stale base hook",
                    source_type=self._spec.source_type,
                    **{key_col_name: getattr(record, key_col_name)},
                )
                continue
            await self._hooks.base.run_all(record)

    async def _handle_base_partial_batch(
        self, parsed_list: list[dict[str, Any]]
    ) -> None:
        """Apply a partial-update base write for a ``base_partial_update`` spec.

        Used by sources with no full-record upsert — instead they patch a
        subset of columns on an existing row. Each message validates into
        ``spec.base_message_model``; its fields minus the discriminators are
        passed as kwargs to the DAO method named by ``spec.base_partial_update``.
        Processes each message independently so a per-record failure does not
        block sibling records — mirrors ``_handle_enrichment_single``.

        Raises:
            MalformedMessageError: If Pydantic validation fails for a message.
            RuntimeError: If the DAO write returns None (DB error).
            BaseRecordNotFoundError: If the target row does not yet exist.
        """
        model = self._spec.base_message_model
        method_name = self._spec.base_partial_update
        assert model is not None  # guaranteed when base_partial_update is set
        assert method_name is not None
        write = getattr(self._dao, method_name)

        for parsed in parsed_list:
            try:
                msg = model.model_validate(parsed)
            except ValidationError as e:
                raise MalformedMessageError(
                    f"{model.__name__} validation failed: {e}"
                ) from e

            kwargs = msg.model_dump(exclude={"source_type", "message_type"})
            result = await asyncio.to_thread(write, **kwargs)
            if result is None:
                raise RuntimeError(
                    f"partial base write failed for "
                    f"source_type={self._spec.source_type!r}"
                )
            if result is False:
                raise BaseRecordNotFoundError(
                    f"base record not yet present for "
                    f"source_type={self._spec.source_type!r}: {kwargs}"
                )

            logger.info(
                "scribe wrote partial base update",
                source_type=self._spec.source_type,
            )
            await self._hooks.base.run_all(msg)

    def _resolve_dropped_columns(self, parsed_list: list[dict[str, Any]]) -> list[str]:
        """Resolve which columns a base batch must drop from its update set.

        For each ``spec.base_column_flags`` entry (flag field -> gated column),
        read the flag off every message (default ``True`` when absent). A batch
        must be uniform on each flag — a mixed batch indicates a routing bug and
        is rejected. When a flag resolves ``False``, its gated column is dropped
        so a base write preserves existing values (e.g. jira's ``services`` when
        service enrichment was skipped).

        Raises:
            MalformedMessageError: If messages disagree on a base-column flag.
        """
        dropped: list[str] = []
        for flag_field, column in self._spec.base_column_flags.items():
            values = {bool(p.get(flag_field, True)) for p in parsed_list}
            if len(values) > 1:
                raise MalformedMessageError(
                    f"Mixed {flag_field} values in a single "
                    f"{self._spec.source_type} batch — routing bug"
                )
            if not values.pop():
                dropped.append(column)
        return dropped

    async def handle_base(self, parsed: dict[str, Any]) -> None:
        """Deserialize and upsert a single base record."""
        await self.handle_base_batch([parsed])

    async def handle_enrichment_batch(self, parsed_list: list[dict[str, Any]]) -> None:
        """Apply LLM field updates for a batch of enrichment messages.

        Processes each record independently so a per-record failure does not
        block sibling records.

        Raises:
            MalformedMessageError: If Pydantic validation fails for a message.
            RuntimeError: If the DAO write returns None (DB error).
            BaseRecordNotFoundError: If the base record does not yet exist.
        """
        for parsed in parsed_list:
            await self._handle_enrichment_single(parsed)

    async def _handle_enrichment_single(self, parsed: dict[str, Any]) -> None:
        model = self._spec.enrichment_message_model
        if model is None:
            raise RuntimeError(
                f"source_type={self._spec.source_type!r} has no enrichment route"
            )
        try:
            msg = model.model_validate(parsed)
        except ValidationError as e:
            raise MalformedMessageError(
                f"{model.__name__} validation failed: {e}"
            ) from e

        key_field = self._spec.enrichment_key
        assert key_field is not None  # guaranteed when enrichment_message_model is set
        key_value = getattr(msg, key_field)
        # Staleness guard (ADR 024): entered_at is separate from the message's
        # LLM fields — it maps to spec.enrichment_entered_at_column, not a
        # literal column name. Absent on messages that predate the guard.
        entered_at = getattr(msg, "entered_at", None)

        # Build the identifier + LLM columns and delegate to the spec-driven DAO.
        update_kwargs: dict[str, Any] = {key_field: key_value}
        for col in self._spec.llm_columns:
            update_kwargs[col] = getattr(msg, col, None)

        outcome = await asyncio.to_thread(
            self._dao.update_llm_fields_from_message,
            entered_at=entered_at,
            **update_kwargs,
        )
        if outcome is WriteOutcome.ERROR:
            raise RuntimeError(f"LLM field update failed for {key_field}={key_value!r}")
        if outcome is WriteOutcome.BASE_NOT_FOUND:
            raise BaseRecordNotFoundError(
                f"base record not yet present for {key_field}={key_value!r}"
            )
        if outcome is WriteOutcome.SKIPPED_STALE:
            # Row exists but this message's entered_at lost the guard — a
            # no-op success (acked, not retried), and stale data means no
            # hook run (the fresher write that beat it already ran its hook).
            logger.info(
                "scribe skipped stale enrichment write",
                source_type=self._spec.source_type,
                **{key_field: key_value},
            )
            return

        logger.info(
            "scribe wrote enrichment",
            source_type=self._spec.source_type,
            **{key_field: key_value},
        )

        await self._run_enrichment_hook(msg, key_value)

    async def _run_enrichment_hook(self, msg: Any, key_value: Any) -> None:
        """Run the post-enrichment hook on the message or the re-fetched record.

        Sources whose hook needs the full record (e.g. Incident.io's
        enigmatologist correlation hook) set ``enrichment_hook_target="record"``
        and a ``record_finder``; others run the hook on the message directly.
        """
        if self._spec.enrichment_hook_target == "message":
            await self._hooks.enrichment.run_all(msg)
            return

        finder_name = self._spec.record_finder
        assert finder_name is not None  # required when hook target is "record"
        finder = getattr(self._dao, finder_name)
        record = await asyncio.to_thread(finder, key_value)
        if record is not None:
            await self._hooks.enrichment.run_all(record)
        else:
            logger.warning(
                "enrichment hook skipped: record not found after write",
                source_type=self._spec.source_type,
                key=str(key_value),
            )

    async def handle_enrichment(self, parsed: dict[str, Any]) -> None:
        """Apply LLM field updates to a single existing record."""
        await self._handle_enrichment_single(parsed)
