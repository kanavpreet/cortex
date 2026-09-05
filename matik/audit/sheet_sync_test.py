"""Tests for the generic audit sheet-sync logic."""

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import common.llm_tracing.operation as operation_mod
from audit.sheet_sync import (
    FINAL,
    ONGOING,
    AuditSpec,
    RunPlan,
    compute_run_plan,
    run_sheet_sync,
)


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of operation._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(operation_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


class _FakeSheetsClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.written: list[dict[str, Any]] = []

    def read_rows(self) -> list[dict[str, Any]]:
        return self.rows

    def upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        self.written.extend(rows)


def _spec(
    entities: list[dict[str, Any]],
    process_result: dict[str, Any] | None = None,
    rescore_result: dict[str, Any] | None = None,
    process_raises: bool = False,
    rescore_raises: bool = False,
) -> AuditSpec:
    def find_eligible_entities(config: object) -> list[dict[str, Any]]:
        return entities

    def process_entity(config: object, entity: dict[str, Any]) -> dict[str, Any]:
        if process_raises:
            raise RuntimeError("boom")
        assert process_result is not None
        return process_result

    def rescore_entity(config: object, row: dict[str, Any]) -> dict[str, Any] | None:
        if rescore_raises:
            raise RuntimeError("boom")
        return rescore_result

    return AuditSpec(
        row_key="reference_id",
        find_eligible_entities=find_eligible_entities,
        process_entity=process_entity,
        rescore_entity=rescore_entity,
    )


# ---------------------------------------------------------------------------
# compute_run_plan
# ---------------------------------------------------------------------------


def test_new_entity_goes_to_process() -> None:
    plan = compute_run_plan(
        entities=[{"reference_id": "INC-1"}],
        sheet_rows=[],
        row_key="reference_id",
    )
    assert plan == RunPlan(to_process=[{"reference_id": "INC-1"}], to_rescore=[])


def test_final_row_is_skipped_entirely() -> None:
    plan = compute_run_plan(
        entities=[{"reference_id": "INC-1"}],
        sheet_rows=[{"reference_id": "INC-1", "overall_status": FINAL}],
        row_key="reference_id",
    )
    assert plan.to_process == []
    assert plan.to_rescore == []


def test_ongoing_row_offered_for_rescore() -> None:
    ongoing_row = {"reference_id": "INC-1", "overall_status": ONGOING}
    plan = compute_run_plan(
        entities=[{"reference_id": "INC-1"}],
        sheet_rows=[ongoing_row],
        row_key="reference_id",
    )
    assert plan.to_process == []
    assert plan.to_rescore == [ongoing_row]


def test_mixed_entities_split_correctly() -> None:
    plan = compute_run_plan(
        entities=[{"reference_id": "INC-1"}, {"reference_id": "INC-2"}],
        sheet_rows=[
            {"reference_id": "INC-1", "overall_status": FINAL},
        ],
        row_key="reference_id",
    )
    assert plan.to_process == [{"reference_id": "INC-2"}]
    assert plan.to_rescore == []


# ---------------------------------------------------------------------------
# run_sheet_sync
# ---------------------------------------------------------------------------


def test_run_sheet_sync_writes_processed_row() -> None:
    processed_row = {"reference_id": "INC-1", "overall_status": FINAL}
    spec = _spec(entities=[{"reference_id": "INC-1"}], process_result=processed_row)
    sheets_client = _FakeSheetsClient(rows=[])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == [processed_row]


def test_run_sheet_sync_opens_root_and_per_entity_spans(
    span_exporter: InMemorySpanExporter,
) -> None:
    """One audit_run root span plus one audit_process_entity span per entity."""
    processed_row = {"reference_id": "INC-1", "overall_status": FINAL}
    spec = _spec(entities=[{"reference_id": "INC-1"}], process_result=processed_row)
    sheets_client = _FakeSheetsClient(rows=[])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    spans = span_exporter.get_finished_spans()
    span_names = [s.name for s in spans]
    assert span_names == ["audit_process_entity", "audit_run"]

    entity_span = next(s for s in spans if s.name == "audit_process_entity")
    assert entity_span.attributes is not None
    assert entity_span.attributes["reference_id"] == "INC-1"

    run_span = next(s for s in spans if s.name == "audit_run")
    assert run_span.attributes is not None
    assert run_span.attributes["processed_count"] == 1
    assert run_span.attributes["rescore_count"] == 0
    assert run_span.attributes["rows_written"] == 1


def test_run_sheet_sync_writes_rescored_row() -> None:
    ongoing_row = {"reference_id": "INC-1", "overall_status": ONGOING}
    rescored_row = {"reference_id": "INC-1", "overall_status": FINAL}
    spec = _spec(entities=[], rescore_result=rescored_row)
    sheets_client = _FakeSheetsClient(rows=[ongoing_row])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == [rescored_row]


def test_run_sheet_sync_skips_rescore_when_still_unresolved() -> None:
    ongoing_row = {"reference_id": "INC-1", "overall_status": ONGOING}
    spec = _spec(entities=[], rescore_result=None)
    sheets_client = _FakeSheetsClient(rows=[ongoing_row])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == []


def test_run_sheet_sync_skips_failed_entity_without_raising() -> None:
    spec = _spec(entities=[{"reference_id": "INC-1"}], process_raises=True)
    sheets_client = _FakeSheetsClient(rows=[])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == []


def test_run_sheet_sync_skips_failed_rescore_without_raising() -> None:
    ongoing_row = {"reference_id": "INC-1", "overall_status": ONGOING}
    spec = _spec(entities=[], rescore_raises=True)
    sheets_client = _FakeSheetsClient(rows=[ongoing_row])

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == []


def test_run_sheet_sync_no_writes_when_nothing_to_do() -> None:
    spec = _spec(entities=[])
    sheets_client = _FakeSheetsClient(
        rows=[{"reference_id": "INC-1", "overall_status": FINAL}]
    )

    run_sheet_sync(spec, config=None, sheets_client=sheets_client)

    assert sheets_client.written == []
