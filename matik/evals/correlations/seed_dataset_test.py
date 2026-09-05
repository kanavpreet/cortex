"""Structural tests for the incident-correlation golden dataset."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from evals import _common as harness
from evals.correlations._common import GoldenRow
from evals.correlations.incident import seed_dataset as sd

_PLANTED_NAMES = ["Priya Raman", "Marcus Feldt", "Dana Whitlock"]


def _all_change_descriptions(row: GoldenRow) -> list[str]:
    events = row["input"]["github_events"] + row["input"]["jira_events"]
    return [e["description"] for e in events]


def _candidate_ids(row: GoldenRow) -> set[str]:
    events = row["input"]["github_events"] + row["input"]["jira_events"]
    return {e["id"] for e in events}


class TestGoldenRows:
    def test_reference_ids_unique(self) -> None:
        ids = [r["metadata"]["reference_id"] for r in sd.GOLDEN_ROWS]
        assert len(ids) == len(set(ids))

    def test_correlated_ids_exist_in_candidate_pool(self) -> None:
        # Every gold ID must be a real candidate in that row's pool.
        for row in sd.GOLDEN_ROWS:
            pool = _candidate_ids(row)
            for cid in row["expected"]["correlated_ids"]:
                assert cid in pool, (
                    f"{row['metadata']['reference_id']}: {cid} not in pool"
                )

    def test_candidate_ids_unique_within_row(self) -> None:
        for row in sd.GOLDEN_ROWS:
            events = row["input"]["github_events"] + row["input"]["jira_events"]
            ids = [e["id"] for e in events]
            assert len(ids) == len(set(ids))

    def test_all_candidates_are_before_the_incident(self) -> None:
        # Production only fetches events before the incident, so the golden pool
        # must never contain after-incident events (no LLM time-filtering to test).
        for row in sd.GOLDEN_ROWS:
            incident_time = datetime.fromisoformat(row["input"]["incident_created_at"])
            events = row["input"]["github_events"] + row["input"]["jira_events"]
            for e in events:
                assert datetime.fromisoformat(e["timestamp"]) <= incident_time, (
                    f"{row['metadata']['reference_id']}: {e['id']} is after the incident"
                )

    def test_score_bands_cover_correlated_ids(self) -> None:
        # Every correlated id must have a valid [lo, hi] band for calibration.
        for row in sd.GOLDEN_ROWS:
            bands = row["expected"]["score_bands"]
            for cid in row["expected"]["correlated_ids"]:
                assert cid in bands, f"{cid} missing a score band"
                lo, hi = bands[cid]
                assert 0.0 <= lo <= hi <= 1.0

    def test_at_least_half_have_no_incident_services(self) -> None:
        # Production usually has no affected_services at correlation time.
        no_services = sum(
            1 for r in sd.GOLDEN_ROWS if not r["input"]["incident_affected_services"]
        )
        assert no_services >= len(sd.GOLDEN_ROWS) // 2

    def test_event_sources_are_mixed(self) -> None:
        # The pool should include rows with both sources, PR-only, and TCMR-only.
        both = github_only = jira_only = 0
        for r in sd.GOLDEN_ROWS:
            gh = bool(r["input"]["github_events"])
            jr = bool(r["input"]["jira_events"])
            both += gh and jr
            github_only += gh and not jr
            jira_only += jr and not gh
        assert both > 0 and github_only > 0 and jira_only > 0

    def test_includes_a_no_correlation_row(self) -> None:
        # The retrieval eval must exercise "select nothing" (tests precision).
        assert any(not r["expected"]["correlated_ids"] for r in sd.GOLDEN_ROWS)

    def test_includes_a_distractor_row(self) -> None:
        # At least one row must have a candidate that is NOT a correct correlation.
        assert any(
            _candidate_ids(r) - set(r["expected"]["correlated_ids"])
            for r in sd.GOLDEN_ROWS
        )

    def test_has_a_pii_example_in_candidates(self) -> None:
        # Planted fake PII should appear in some candidate description.
        joined = " ".join(
            d for r in sd.GOLDEN_ROWS for d in _all_change_descriptions(r)
        )
        assert any(name in joined for name in _PLANTED_NAMES)


def test_main_seeds_every_row() -> None:
    # init_sdk / init_dataset now live in the shared seed helper (evals._common).
    dataset = MagicMock()
    with (
        patch.object(harness, "init_sdk"),
        patch.object(harness, "init_dataset", return_value=dataset) as init_dataset,
    ):
        sd.main()
    kwargs = init_dataset.call_args.kwargs
    assert kwargs["name"] == sd.DATASET_NAME
    assert kwargs["metadata"]["category"] == "correlation"
    assert kwargs["description"]
    assert dataset.insert.call_count == len(sd.GOLDEN_ROWS)
    dataset.flush.assert_called_once()
