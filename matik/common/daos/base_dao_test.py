"""Tests for BaseUpsertDAO spec-driven behavior.

These cover the base-class branches that are not exercised through the concrete
source DAOs' public methods: the ``dropped_columns`` update-set filter, the
``enrichment_overwrites_with_none`` toggle, and the ``enrichment_key`` guard.
The concrete DAO suites (incidentio/jira/ghe_pr) act as the write-oracle; these
tests pin the generic mechanics the specs rely on.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from common.daos.base_dao import BaseUpsertDAO, WriteOutcome
from common.datasources.registry import DataSourceSpec
from common.models.jira_issue_record import JiraIssueRecord

if TYPE_CHECKING:
    from sqlalchemy import Engine, Table


@pytest.fixture
def mock_engine() -> MagicMock:
    """Mock SQLAlchemy engine with a connection context manager."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None
    return engine


def _jira_spec(**overrides: object) -> DataSourceSpec:
    """Build a jira-shaped spec (real table) with optional field overrides."""
    kwargs: dict[str, object] = {
        "source_type": "_base_dao_test",
        "record_model": JiraIssueRecord,
        "conflict_keys": ["issue_key"],
        "enrichment_key": "issue_key",
    }
    kwargs.update(overrides)
    return DataSourceSpec(**kwargs)  # type: ignore[arg-type]


class TestUpsertBatchDroppedColumns:
    """upsert_batch drops the requested columns from the ON DUPLICATE KEY UPDATE set."""

    def test_dropped_column_excluded_from_update_clause(
        self, mock_engine: MagicMock
    ) -> None:
        """A dropped column is absent from the compiled UPDATE clause."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        issue = JiraIssueRecord(
            issue_id="1",
            issue_key="OPS-1",
            ticket_type="tcmr",
            summary="s",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            services=["svc-a"],
        )
        dao.upsert_batch([issue], dropped_columns=["services"])

        sql_text = str(conn.execute.call_args[0][0])
        assert "services = VALUES(services)" not in sql_text
        # A non-dropped mutable column is still updated.
        assert "summary = VALUES(summary)" in sql_text

    def test_no_dropped_columns_keeps_full_update_set(
        self, mock_engine: MagicMock
    ) -> None:
        """Without dropped_columns, every mutable column stays in the UPDATE set."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        issue = JiraIssueRecord(
            issue_id="1",
            issue_key="OPS-1",
            ticket_type="tcmr",
            summary="s",
            status_name="Open",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            services=["svc-a"],
        )
        dao.upsert_batch([issue])

        sql_text = str(conn.execute.call_args[0][0])
        assert "services = VALUES(services)" in sql_text


class TestUpdateLLMFieldsFromMessage:
    """update_llm_fields_from_message honors the overwrite-with-none toggle."""

    def test_none_values_dropped_by_default(self, mock_engine: MagicMock) -> None:
        """By default a None LLM value is excluded from the UPDATE statement."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        dao.update_llm_fields_from_message(
            issue_key="OPS-1", summary_hash="abc", issue_summary=None
        )

        sql_text = str(conn.execute.call_args[0][0])
        assert "summary_hash" in sql_text
        assert "issue_summary" not in sql_text

    def test_all_none_short_circuits_without_db_call(
        self, mock_engine: MagicMock
    ) -> None:
        """When every value is None (and dropping is on), no DB call is made."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        result = dao.update_llm_fields_from_message(
            issue_key="OPS-1", issue_summary=None, summary_hash=None
        )

        assert result is WriteOutcome.WRITTEN
        conn.execute.assert_not_called()

    def test_overwrites_with_none_keeps_none_values(
        self, mock_engine: MagicMock
    ) -> None:
        """With the toggle on, None values ARE written (blanking the column)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        dao = BaseUpsertDAO(
            _jira_spec(enrichment_overwrites_with_none=True), mock_engine
        )

        dao.update_llm_fields_from_message(
            issue_key="OPS-1", issue_summary=None, summary_hash=None
        )

        # A DB call is made even though both values are None.
        conn.execute.assert_called_once()
        sql_text = str(conn.execute.call_args[0][0])
        assert "issue_summary" in sql_text
        assert "summary_hash" in sql_text

    def test_missing_enrichment_key_raises(self, mock_engine: MagicMock) -> None:
        """A spec with no enrichment_key cannot run an LLM update."""
        dao = BaseUpsertDAO(_jira_spec(enrichment_key=None), mock_engine)

        with pytest.raises(RuntimeError, match="no enrichment_key"):
            dao.update_llm_fields_from_message(issue_key="OPS-1", summary_hash="abc")


class TestJsonColumnRoundTrip:
    """Regression: JSON/list columns must round-trip as lists, not double-encoded strings.

    Guards against the double-encode bug where ``_derived_row`` pre-encoded JSON
    columns with ``json.dumps`` and SQLAlchemy's ``JSON`` type then encoded them
    AGAIN on write, so the DB stored ``'["a"]'`` (a JSON string) instead of
    ``["a"]`` (a JSON array). The MagicMock-based suites can't catch this — they
    never store/read a value, so serialization is invisible to them.

    ``_derived_row``'s contract is that JSON columns come out as native Python
    objects (``list``/``None``) with NO pre-encoding, leaving the single encode to
    SQLAlchemy's ``JSON`` type. This test verifies that end-to-end against a real
    (in-memory SQLite) ``JSON`` column: whatever ``_derived_row`` emits is written
    through a typed ``JSON`` column and must land as a JSON array and read back as
    a list. The ``JSON`` bind/result behavior is dialect-agnostic (encode once on
    write, decode once on read), so SQLite faithfully reproduces MySQL's behavior;
    a standalone table is used because the real table's MySQL-only audit-column DDL
    (``ON UPDATE CURRENT_TIMESTAMP``) and ``ON DUPLICATE KEY UPDATE`` don't compile
    on SQLite.
    """

    def _record(self, services: list[str] | None) -> JiraIssueRecord:
        return JiraIssueRecord(
            issue_id="10001",
            issue_key="OPS-1",
            ticket_type="operational",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            services=services,
            tcmr_related_services=services,
        )

    def _roundtrip_json_column(
        self, column: str, services: list[str] | None
    ) -> tuple[object, object]:
        """Write ``_derived_row``'s value for ``column`` through a real JSON column.

        Builds the row exactly as the batch upsert would (via ``_derived_row``),
        then persists that value through a standalone typed ``JSON`` column on
        SQLite. Returns ``(raw_stored_text, typed_read_value)`` so the test can
        assert both the on-disk bytes (must be a JSON array, not a double-encoded
        string) and the deserialized Python value (must be a ``list``/``None``).
        """
        from sqlalchemy import (
            Column,
            Integer,
            MetaData,
            String,
            Table,
            create_engine,
            text,
        )
        from sqlalchemy.types import JSON

        # BaseUpsertDAO needs a real table on the spec to build; a MagicMock engine
        # is enough since we never execute against it — we only call _derived_row.
        dao = BaseUpsertDAO(_jira_spec(), MagicMock())
        row = dao._derived_row(self._record(services))
        value = row[column]

        engine = create_engine("sqlite://")
        md = MetaData()
        t = Table(
            "t",
            md,
            Column("id", Integer, primary_key=True),
            Column("issue_key", String(255)),
            Column("val", JSON),
        )
        md.create_all(engine)
        with engine.begin() as conn:
            conn.execute(t.insert().values(id=1, issue_key="OPS-1", val=value))

        with engine.connect() as conn:
            raw = conn.execute(text("SELECT val FROM t WHERE id = 1")).scalar_one()
            typed = conn.execute(t.select().where(t.c.id == 1)).mappings().one()["val"]
        return raw, typed

    def test_list_column_stored_as_json_array_not_string(self) -> None:
        """A list JSON column is stored as a JSON array and reads back as a list."""
        raw, typed = self._roundtrip_json_column("services", ["svc-a", "svc-b"])

        # Raw stored bytes must be a JSON array, NOT a double-encoded string.
        assert raw == '["svc-a", "svc-b"]', f"double-encoded: {raw!r}"
        # A typed read deserializes exactly once, yielding a real list.
        assert typed == ["svc-a", "svc-b"]
        assert isinstance(typed, list)

    def test_all_json_columns_encoded_once(self) -> None:
        """Every JSON column on the model round-trips as a list, not a string."""
        for column in ("services", "tcmr_related_services"):
            raw, typed = self._roundtrip_json_column(column, ["a", "b"])
            assert raw == '["a", "b"]', f"{column} double-encoded: {raw!r}"
            assert typed == ["a", "b"], f"{column} did not read back as a list"
            assert isinstance(typed, list)

    def test_none_list_column_passed_through_and_reads_back_none(self) -> None:
        """A None JSON column is handed to SQLAlchemy as native None (not pre-encoded).

        ``_derived_row`` must emit ``None`` unchanged — not ``json.dumps(None)`` (the
        string ``'null'``) — so the column type owns null handling and the value
        reads back as ``None``.
        """
        dao = BaseUpsertDAO(_jira_spec(), MagicMock())
        assert dao._derived_row(self._record(None))["services"] is None

        _raw, typed = self._roundtrip_json_column("services", None)
        assert typed is None


class TestStalenessGuardUpsert:
    """Compiled-SQL assertions for the base-upsert case() guard (ADR 024).

    ``INSERT ... ON DUPLICATE KEY UPDATE`` is MySQL-specific and doesn't
    compile on SQLite (see ``TestJsonColumnRoundTrip``), so this inspects the
    compiled statement text via a MagicMock engine, matching the style already
    used for the upsert path elsewhere in this file and in the concrete DAO
    suites.
    """

    def _issue(self, **overrides: object) -> JiraIssueRecord:
        defaults: dict[str, object] = {
            "issue_id": "1",
            "issue_key": "OPS-1",
            "ticket_type": "tcmr",
            "summary": "s",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        defaults.update(overrides)
        return JiraIssueRecord(**defaults)

    def test_guarded_spec_wraps_columns_in_case_when(
        self, mock_engine: MagicMock
    ) -> None:
        """A spec with base_entered_at_column set guards every upsert column."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        spec = _jira_spec(base_entered_at_column="jira_base_entered_at")
        dao = BaseUpsertDAO(spec, mock_engine)

        dao.upsert_batch([self._issue(jira_base_entered_at=datetime(2026, 1, 1))])

        sql_text = str(conn.execute.call_args[0][0])
        assert "summary = CASE WHEN" in sql_text
        assert "THEN VALUES(summary) ELSE jira_issues.summary END" in sql_text
        # The guard timestamp column advances alongside a winning write, but
        # its own CASE is narrower than a payload column's: it only takes the
        # incoming value when that value is non-NULL *and* fresher — never on
        # a bare "incoming is NULL" backward-compat pass-through (that would
        # regress the stored guard to NULL; see
        # test_null_entered_at_never_regresses_guard_column below).
        assert (
            "jira_base_entered_at = CASE WHEN"
            " (VALUES(jira_base_entered_at) IS NOT NULL AND"
            " (jira_issues.jira_base_entered_at IS NULL OR"
            " VALUES(jira_base_entered_at) > jira_issues.jira_base_entered_at))"
            " THEN VALUES(jira_base_entered_at)"
            " ELSE jira_issues.jira_base_entered_at END" in sql_text
        )

    def test_unguarded_spec_keeps_plain_values_mapping(
        self, mock_engine: MagicMock
    ) -> None:
        """No base_entered_at_column: stays unconditional (backward compat)."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        dao.upsert_batch([self._issue()])

        sql_text = str(conn.execute.call_args[0][0])
        assert "summary = VALUES(summary)" in sql_text
        assert "CASE WHEN" not in sql_text

    def test_null_entered_at_never_regresses_guard_column(
        self, mock_engine: MagicMock
    ) -> None:
        """A message with no entered_at (predates the guard) writes its payload
        columns unconditionally, per backward-compat, but must NOT reset the
        stored guard timestamp back to NULL — that would un-protect the row,
        letting a later, genuinely stale redrive land against a NULL guard and
        win. Regression test: this previously compiled the guard column into
        the same "incoming IS NULL -> take incoming (NULL)" CASE as payload
        columns, silently wiping a real stored timestamp."""
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.return_value.rowcount = 1
        spec = _jira_spec(base_entered_at_column="jira_base_entered_at")
        dao = BaseUpsertDAO(spec, mock_engine)

        dao.upsert_batch([self._issue(jira_base_entered_at=None)])

        stmt = conn.execute.call_args[0][0]
        sql_text = str(stmt)
        # The payload column still takes the unconditional-write branch.
        assert "summary = CASE WHEN" in sql_text
        # The guard column's own CASE requires the incoming value to be
        # non-NULL before it will overwrite the stored one.
        assert "jira_base_entered_at = CASE WHEN" in sql_text
        assert "VALUES(jira_base_entered_at) IS NOT NULL AND" in sql_text
        # The incoming row really did carry entered_at=None (not omitted).
        assert stmt.compile().params["jira_base_entered_at_m0"] is None


class TestStalenessGuardPartialUpdate:
    """Real-DB behavior of the entered_at guard on ``_update_partial`` /
    ``update_llm_fields_from_message`` (ADR 024).

    Unlike the upsert path's ``ON DUPLICATE KEY UPDATE``, the guarded
    ``UPDATE ... WHERE`` statement is dialect-agnostic SQLAlchemy Core, so
    this runs against a real (in-memory) SQLite table to verify actual
    read/write outcomes, not just compiled SQL text. Uses a standalone table
    (not the real ``jira_issues`` table) for the same reason
    ``TestJsonColumnRoundTrip`` does: the real table's MySQL-only audit-column
    DDL doesn't compile on SQLite. ``BaseUpsertDAO._table`` is reassigned
    directly after construction since ``_update_partial``/
    ``update_llm_fields_from_message`` only ever touch ``self._table``, not
    the spec's ``record_model`` class.
    """

    def _make_dao(self) -> tuple[BaseUpsertDAO, "Table", "Engine"]:
        from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
        from sqlalchemy import create_engine as _create_engine

        engine = _create_engine("sqlite://")
        md = MetaData()
        table = Table(
            "t",
            md,
            Column("id", Integer, primary_key=True),
            Column("issue_key", String(255), unique=True),
            Column("summary_hash", String(64)),
            Column("jira_enrichment_entered_at", DateTime),
        )
        md.create_all(engine)
        dao = BaseUpsertDAO(
            _jira_spec(enrichment_entered_at_column="jira_enrichment_entered_at"),
            engine,
        )
        dao._table = table
        return dao, table, engine

    def _insert(
        self,
        engine: "Engine",
        table: "Table",
        issue_key: str,
        summary_hash: str | None,
        entered_at: datetime | None,
    ) -> None:
        with engine.begin() as conn:
            conn.execute(
                table.insert().values(
                    issue_key=issue_key,
                    summary_hash=summary_hash,
                    jira_enrichment_entered_at=entered_at,
                )
            )

    def _read(
        self, engine: "Engine", table: "Table", issue_key: str
    ) -> dict[str, object]:
        with engine.connect() as conn:
            row = (
                conn.execute(table.select().where(table.c.issue_key == issue_key))
                .mappings()
                .one()
            )
            return dict(row)

    def test_base_not_found_when_row_absent(self) -> None:
        dao, _table, _engine = self._make_dao()

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1", summary_hash="new", entered_at=datetime(2026, 1, 1)
        )

        assert outcome is WriteOutcome.BASE_NOT_FOUND

    def test_fresher_entered_at_writes_and_advances_guard(self) -> None:
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", "old", datetime(2026, 1, 1))

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1", summary_hash="new", entered_at=datetime(2026, 1, 2)
        )

        assert outcome is WriteOutcome.WRITTEN
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "new"
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 2)

    def test_stale_entered_at_skipped_without_overwriting(self) -> None:
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", "current", datetime(2026, 1, 5))

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1",
            summary_hash="stale-retry",
            entered_at=datetime(2026, 1, 1),
        )

        assert outcome is WriteOutcome.SKIPPED_STALE
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "current"
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 5)

    def test_null_stored_guard_always_accepts_first_write(self) -> None:
        """A never-before-guarded row (NULL stored timestamp) always accepts."""
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", None, None)

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1",
            summary_hash="first-guarded-write",
            entered_at=datetime(2026, 1, 1),
        )

        assert outcome is WriteOutcome.WRITTEN
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "first-guarded-write"
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 1)

    def test_missing_entered_at_falls_back_to_unconditional_write(self) -> None:
        """A message with no entered_at (predates the guard) always overwrites,
        even when it would otherwise lose to a fresher stored timestamp."""
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", "current", datetime(2026, 1, 5))

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1", summary_hash="unconditional", entered_at=None
        )

        assert outcome is WriteOutcome.WRITTEN
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "unconditional"
        # No entered_at was supplied, so the guard column itself is untouched.
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 5)

    def test_older_retry_loses_to_a_fresher_write_that_already_landed(self) -> None:
        """The core DLQ scenario (ADR 024): a fresher write lands first, then a
        stale redriven message with an older entered_at arrives and loses."""
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", "seed", datetime(2026, 1, 1))

        fresh_outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1", summary_hash="fresh", entered_at=datetime(2026, 1, 10)
        )
        assert fresh_outcome is WriteOutcome.WRITTEN

        stale_redrive_outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1",
            summary_hash="stale-redrive",
            entered_at=datetime(2026, 1, 2),
        )
        assert stale_redrive_outcome is WriteOutcome.SKIPPED_STALE

        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "fresh"

    def test_race_row_inserted_between_update_and_probe_retries_and_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for the probe-race: a concurrent base write can
        insert the target row in the gap between the initial guarded UPDATE
        (which affects 0 rows because the row doesn't exist yet) and the
        follow-up disambiguation probe. The probe must not misread "row
        absent when the UPDATE ran" as "row present but stale" just because
        it now sees a row — it must recognize the guard column doesn't
        actually block the write and retry."""
        from common.daos import base_dao as base_dao_module
        from common.utils.retry_utils import fetch_all_with_retry as original_fetch

        dao, table, engine = self._make_dao()
        # No insert yet: the row does not exist when the first UPDATE runs.

        calls = {"n": 0}

        def racing_fetch_all_with_retry(
            engine_arg: "Engine", stmt: object, op: str | None = None
        ) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                # A concurrent base writer inserts the row in the gap
                # between the initial UPDATE and this probe. Its own
                # write-group leaves this enrichment guard column NULL.
                self._insert(engine, table, "OPS-1", None, None)
            return original_fetch(engine_arg, stmt, op=op)  # type: ignore[arg-type]

        monkeypatch.setattr(
            base_dao_module, "fetch_all_with_retry", racing_fetch_all_with_retry
        )

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1",
            summary_hash="legit-update",
            entered_at=datetime(2026, 1, 1),
        )

        assert outcome is WriteOutcome.WRITTEN
        assert calls["n"] == 1
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] == "legit-update"
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 1)

    def test_race_that_persists_through_retry_is_reported_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a second, genuinely fresher write wins in the gap between the
        probe and the retry, the retry legitimately loses too — this must
        resolve to SKIPPED_STALE, not an infinite retry loop."""
        from common.daos import base_dao as base_dao_module
        from common.utils.retry_utils import (
            execute_with_retry as original_execute,
        )
        from common.utils.retry_utils import (
            fetch_all_with_retry as original_fetch,
        )

        dao, table, engine = self._make_dao()

        def racing_fetch_all_with_retry(
            engine_arg: "Engine", stmt: object, op: str | None = None
        ) -> object:
            # The row appears (guard column NULL) just before the probe
            # reads it — same setup as the test above.
            self._insert(engine, table, "OPS-1", None, None)
            return original_fetch(engine_arg, stmt, op=op)  # type: ignore[arg-type]

        monkeypatch.setattr(
            base_dao_module, "fetch_all_with_retry", racing_fetch_all_with_retry
        )

        def racing_execute_with_retry(
            engine_arg: "Engine", stmt: object, op: str | None = None
        ) -> object:
            if op is not None and op.startswith("update_partial_retry"):
                # A second, genuinely fresher write beats us to it right
                # before our own retry executes.
                with engine.begin() as conn:
                    conn.execute(
                        table.update()
                        .where(table.c.issue_key == "OPS-1")
                        .values(jira_enrichment_entered_at=datetime(2026, 1, 9))
                    )
            return original_execute(engine_arg, stmt, op=op)  # type: ignore[arg-type]

        monkeypatch.setattr(
            base_dao_module, "execute_with_retry", racing_execute_with_retry
        )

        outcome = dao.update_llm_fields_from_message(
            issue_key="OPS-1",
            summary_hash="stale-retry",
            entered_at=datetime(2026, 1, 1),
        )

        assert outcome is WriteOutcome.SKIPPED_STALE
        row = self._read(engine, table, "OPS-1")
        assert row["summary_hash"] is None
        assert row["jira_enrichment_entered_at"] == datetime(2026, 1, 9)


class TestFindStaleBaseKeys:
    """Real-DB behavior of ``find_stale_base_keys`` (ADR 024) — the read-back
    ``GenericHandler.handle_base_batch`` uses to decide which just-upserted
    records lost the base staleness guard and must not fire a post-write
    hook (e.g. incidentio's enigmatologist correlation hook)."""

    def _issue(self, **overrides: object) -> JiraIssueRecord:
        defaults: dict[str, object] = {
            "issue_id": "1",
            "issue_key": "OPS-1",
            "ticket_type": "tcmr",
            "summary": "s",
            "status_name": "Open",
            "created_at": datetime(2024, 1, 15, 10, 30, 0),
        }
        defaults.update(overrides)
        return JiraIssueRecord(**defaults)

    def _make_dao(self) -> tuple[BaseUpsertDAO, "Table", "Engine"]:
        from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
        from sqlalchemy import create_engine as _create_engine

        engine = _create_engine("sqlite://")
        md = MetaData()
        table = Table(
            "t",
            md,
            Column("id", Integer, primary_key=True),
            Column("issue_key", String(255), unique=True),
            Column("jira_base_entered_at", DateTime),
        )
        md.create_all(engine)
        dao = BaseUpsertDAO(
            _jira_spec(base_entered_at_column="jira_base_entered_at"),
            engine,
        )
        dao._table = table
        return dao, table, engine

    def _insert(
        self,
        engine: "Engine",
        table: "Table",
        issue_key: str,
        entered_at: datetime | None,
    ) -> None:
        with engine.begin() as conn:
            conn.execute(
                table.insert().values(
                    issue_key=issue_key, jira_base_entered_at=entered_at
                )
            )

    def test_no_guard_column_short_circuits_without_query(
        self, mock_engine: MagicMock
    ) -> None:
        dao = BaseUpsertDAO(_jira_spec(), mock_engine)

        stale = dao.find_stale_base_keys([self._issue()])

        assert stale == set()
        mock_engine.connect.assert_not_called()

    def test_record_with_no_entered_at_short_circuits_without_query(
        self, mock_engine: MagicMock
    ) -> None:
        spec = _jira_spec(base_entered_at_column="jira_base_entered_at")
        dao = BaseUpsertDAO(spec, mock_engine)

        stale = dao.find_stale_base_keys([self._issue(jira_base_entered_at=None)])

        assert stale == set()
        mock_engine.connect.assert_not_called()

    def test_record_that_lost_the_guard_is_flagged_stale(self) -> None:
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", datetime(2026, 1, 10))
        record = self._issue(
            issue_key="OPS-1", jira_base_entered_at=datetime(2026, 1, 1)
        )

        stale = dao.find_stale_base_keys([record])

        assert stale == {"OPS-1"}

    def test_record_that_won_the_guard_is_not_flagged(self) -> None:
        dao, table, engine = self._make_dao()
        self._insert(engine, table, "OPS-1", datetime(2026, 1, 10))
        record = self._issue(
            issue_key="OPS-1", jira_base_entered_at=datetime(2026, 1, 10)
        )

        stale = dao.find_stale_base_keys([record])

        assert stale == set()
