"""Unit tests for CorrelationMetrics."""

import time
from unittest.mock import MagicMock

from common.metrics.correlation_metrics import (
    COUNT_BUCKETS,
    NODE_DURATION_BUCKETS,
    SCORE_BUCKETS,
    CorrelationMetrics,
)


def _make_metrics(meter: MagicMock) -> CorrelationMetrics:
    return CorrelationMetrics(meter, "enigmatologist", "reliability", "incident")


class TestCorrelationMetricsConstants:
    """Test module constants."""

    def test_score_buckets(self) -> None:
        """Score buckets cover 0.1 to 1.0 in 0.1 increments."""
        assert SCORE_BUCKETS == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
        for i in range(len(SCORE_BUCKETS) - 1):
            assert SCORE_BUCKETS[i] < SCORE_BUCKETS[i + 1]

    def test_count_buckets(self) -> None:
        """Count buckets are defined correctly."""
        assert COUNT_BUCKETS == (0, 1, 2, 5, 10, 20, 50, 100)


class TestCorrelationMetrics:
    """Test suite for CorrelationMetrics."""

    def _create_mock_meter(self) -> MagicMock:
        """Create a mock meter with mock instruments."""
        meter = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_counter.return_value = MagicMock()
        return meter

    def test_init_creates_instruments(self) -> None:
        """Init creates all required histogram and counter instruments."""
        meter = self._create_mock_meter()
        _make_metrics(meter)

        histogram_names = [c[1]["name"] for c in meter.create_histogram.call_args_list]
        assert "matik_correlation_candidate_events" in histogram_names
        assert "matik_correlation_run_matches" in histogram_names
        assert "matik_correlation_score" in histogram_names
        assert "matik_correlation_duration_seconds" in histogram_names
        assert "matik_correlation_node_duration_seconds" in histogram_names

        counter_names = [c[1]["name"] for c in meter.create_counter.call_args_list]
        assert "matik_correlation_outcomes_total" in counter_names

    def test_record_candidates_evaluated_records_both_sources(self) -> None:
        """GitHub and Jira candidates are recorded as separate metric entries."""
        meter = self._create_mock_meter()
        candidates_evaluated = MagicMock()
        meter.create_histogram.side_effect = [
            candidates_evaluated,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_candidates_evaluated(github_count=12, jira_count=5)

        assert candidates_evaluated.record.call_count == 2
        calls = candidates_evaluated.record.call_args_list
        github_call = next(c for c in calls if c[0][1]["source"] == "biztech_github")
        jira_call = next(c for c in calls if c[0][1]["source"] == "jira")
        assert github_call[0][0] == 12
        assert jira_call[0][0] == 5

    def test_record_candidates_evaluated_labels(self) -> None:
        """All source records carry service, type, and anchor labels."""
        meter = self._create_mock_meter()
        candidates_evaluated = MagicMock()
        meter.create_histogram.side_effect = [
            candidates_evaluated,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_candidates_evaluated(github_count=1, jira_count=1)

        for call in candidates_evaluated.record.call_args_list:
            attrs = call[0][1]
            assert attrs["service"] == "enigmatologist"
            assert attrs["type"] == "reliability"
            assert attrs["anchor"] == "incident"

    def test_record_candidates_evaluated_zero_counts(self) -> None:
        """Zero counts are still recorded (histogram tracks 0 as a value)."""
        meter = self._create_mock_meter()
        candidates_evaluated = MagicMock()
        meter.create_histogram.side_effect = [
            candidates_evaluated,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_candidates_evaluated(github_count=0, jira_count=0)

        assert candidates_evaluated.record.call_count == 2

    def test_record_run_outcome_service_only(self) -> None:
        """service_only outcome increments counter and records service match count."""
        meter = self._create_mock_meter()
        run_outcomes = MagicMock()
        matches_per_run = MagicMock()
        meter.create_counter.return_value = run_outcomes
        meter.create_histogram.side_effect = [
            MagicMock(),
            matches_per_run,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_outcome(
            "service_only", service_match_count=3, llm_match_count=0
        )

        run_outcomes.add.assert_called_once()
        attrs = run_outcomes.add.call_args[0][1]
        assert attrs["outcome"] == "service_only"
        assert attrs["service"] == "enigmatologist"
        assert attrs["type"] == "reliability"
        assert attrs["anchor"] == "incident"
        assert matches_per_run.record.call_args[0][0] == 3

    def test_record_run_outcome_matches_per_run_labels(self) -> None:
        """matches_per_run histogram carries service, type, and anchor labels."""
        meter = self._create_mock_meter()
        matches_per_run = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            matches_per_run,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_outcome(
            "service_only", service_match_count=1, llm_match_count=0
        )

        match_attrs = matches_per_run.record.call_args[0][1]
        assert match_attrs["service"] == "enigmatologist"
        assert match_attrs["type"] == "reliability"
        assert match_attrs["anchor"] == "incident"

    def test_record_run_outcome_llm_only(self) -> None:
        """llm_only outcome records LLM match count as total."""
        meter = self._create_mock_meter()
        run_outcomes = MagicMock()
        matches_per_run = MagicMock()
        meter.create_counter.return_value = run_outcomes
        meter.create_histogram.side_effect = [
            MagicMock(),
            matches_per_run,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_outcome("llm_only", service_match_count=0, llm_match_count=2)

        assert run_outcomes.add.call_args[0][1]["outcome"] == "llm_only"
        assert matches_per_run.record.call_args[0][0] == 2

    def test_record_run_outcome_both(self) -> None:
        """both outcome sums service and LLM counts as total matches."""
        meter = self._create_mock_meter()
        run_outcomes = MagicMock()
        matches_per_run = MagicMock()
        meter.create_counter.return_value = run_outcomes
        meter.create_histogram.side_effect = [
            MagicMock(),
            matches_per_run,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_outcome("both", service_match_count=2, llm_match_count=3)

        assert run_outcomes.add.call_args[0][1]["outcome"] == "both"
        assert matches_per_run.record.call_args[0][0] == 5  # 2 + 3

    def test_record_run_outcome_none(self) -> None:
        """none outcome records zero total matches."""
        meter = self._create_mock_meter()
        matches_per_run = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            matches_per_run,
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_outcome("none", service_match_count=0, llm_match_count=0)

        assert matches_per_run.record.call_args[0][0] == 0

    def test_record_match_scores_llm(self) -> None:
        """Each score is recorded individually with LLM correlation_type label."""
        meter = self._create_mock_meter()
        match_score = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            match_score,
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_match_scores([0.75, 0.82, 0.91], "LLM")

        assert match_score.record.call_count == 3
        scores = [c[0][0] for c in match_score.record.call_args_list]
        assert sorted(scores) == sorted([0.75, 0.82, 0.91])
        for call in match_score.record.call_args_list:
            attrs = call[0][1]
            assert attrs["correlation_type"] == "LLM"
            assert attrs["service"] == "enigmatologist"
            assert attrs["type"] == "reliability"
            assert attrs["anchor"] == "incident"

    def test_record_match_scores_service_match(self) -> None:
        """SERVICE_MATCH label is applied correctly."""
        meter = self._create_mock_meter()
        match_score = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            match_score,
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_match_scores([0.9], "SERVICE_MATCH")

        assert match_score.record.call_args[0][1]["correlation_type"] == "SERVICE_MATCH"

    def test_record_match_scores_empty_list(self) -> None:
        """Empty scores list records nothing."""
        meter = self._create_mock_meter()
        match_score = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            match_score,
            MagicMock(),
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_match_scores([], "LLM")

        match_score.record.assert_not_called()

    def test_record_run_duration(self) -> None:
        """record_run_duration records elapsed time with service/type/anchor/outcome labels."""
        meter = self._create_mock_meter()
        run_duration = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            run_duration,
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_duration(3.5, "service_only")

        run_duration.record.assert_called_once()
        value, attrs = run_duration.record.call_args[0]
        assert value == 3.5
        assert attrs["service"] == "enigmatologist"
        assert attrs["type"] == "reliability"
        assert attrs["anchor"] == "incident"
        assert attrs["outcome"] == "service_only"

    def test_record_run_duration_default_outcome(self) -> None:
        """record_run_duration defaults to 'unknown' outcome when not provided."""
        meter = self._create_mock_meter()
        run_duration = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            run_duration,
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        metrics.record_run_duration(1.0)

        attrs = run_duration.record.call_args[0][1]
        assert attrs["outcome"] == "unknown"

    def test_start_run_records_measured_duration(self) -> None:
        """start_run returns a recorder that accepts outcome and logs elapsed time."""
        meter = self._create_mock_meter()
        run_duration = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            run_duration,
            MagicMock(),
        ]

        metrics = _make_metrics(meter)
        record = metrics.start_run()
        time.sleep(0.02)
        record("llm_only")

        run_duration.record.assert_called_once()
        value, attrs = run_duration.record.call_args[0]
        assert value >= 0.02
        assert attrs["outcome"] == "llm_only"

    def test_record_node_duration(self) -> None:
        """record_node_duration records elapsed time with node label."""
        meter = self._create_mock_meter()
        node_duration = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            node_duration,
        ]

        metrics = _make_metrics(meter)
        metrics.record_node_duration("fetch_jira_issues", 0.75)

        node_duration.record.assert_called_once()
        value, attrs = node_duration.record.call_args[0]
        assert value == 0.75
        assert attrs["service"] == "enigmatologist"
        assert attrs["type"] == "reliability"
        assert attrs["anchor"] == "incident"
        assert attrs["node"] == "fetch_jira_issues"

    def test_start_node_records_measured_duration(self) -> None:
        """start_node returns a recorder that logs the measured elapsed time."""
        meter = self._create_mock_meter()
        node_duration = MagicMock()
        meter.create_histogram.side_effect = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            node_duration,
        ]

        metrics = _make_metrics(meter)
        record = metrics.start_node("fetch_biztech_github_prs")
        time.sleep(0.02)
        record()

        node_duration.record.assert_called_once()
        value, attrs = node_duration.record.call_args[0]
        assert value >= 0.02
        assert attrs["node"] == "fetch_biztech_github_prs"


class TestCorrelationMetricsInstrumentDescriptions:
    """Test instrument descriptions and units."""

    def test_histogram_descriptions(self) -> None:
        """Histograms have proper descriptions, units, and bucket boundaries."""
        meter = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_counter.return_value = MagicMock()

        CorrelationMetrics(meter, "enigmatologist", "reliability", "incident")

        hist_calls = {c[1]["name"]: c[1] for c in meter.create_histogram.call_args_list}

        assert hist_calls["matik_correlation_candidate_events"]["unit"] == "{event}"
        assert (
            hist_calls["matik_correlation_candidate_events"][
                "explicit_bucket_boundaries_advisory"
            ]
            == COUNT_BUCKETS
        )

        assert hist_calls["matik_correlation_run_matches"]["unit"] == "{match}"
        assert (
            hist_calls["matik_correlation_run_matches"][
                "explicit_bucket_boundaries_advisory"
            ]
            == COUNT_BUCKETS
        )

        assert hist_calls["matik_correlation_score"]["unit"] == "1"
        assert (
            hist_calls["matik_correlation_score"]["explicit_bucket_boundaries_advisory"]
            == SCORE_BUCKETS
        )

        assert hist_calls["matik_correlation_node_duration_seconds"]["unit"] == "s"
        assert (
            hist_calls["matik_correlation_node_duration_seconds"][
                "explicit_bucket_boundaries_advisory"
            ]
            == NODE_DURATION_BUCKETS
        )

    def test_counter_description(self) -> None:
        """Run outcomes counter has proper description and unit."""
        meter = MagicMock()
        meter.create_histogram.return_value = MagicMock()
        meter.create_counter.return_value = MagicMock()

        CorrelationMetrics(meter, "enigmatologist", "reliability", "incident")

        counter_call = meter.create_counter.call_args[1]
        assert counter_call["name"] == "matik_correlation_outcomes_total"
        assert counter_call["unit"] == "{run}"
