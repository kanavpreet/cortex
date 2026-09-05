"""Correlation metrics for the enigmatologist service.

Provides metrics for tracking correlation outcomes including:
- Score distributions (histograms, dupe-safe)
- Candidate change events evaluated
- Matches found per run
- Outcome breakdown by correlation path
- End-to-end correlation run duration
"""

import time
from collections.abc import Callable
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Histogram buckets for correlation scores (0.0 - 1.0)
SCORE_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)

# Histogram buckets for candidate/match counts per run
COUNT_BUCKETS = (0, 1, 2, 5, 10, 20, 50, 100)

# Histogram buckets for correlation run duration in seconds. A run executes the
# LangGraph pipeline (data fetch + service scoring + LLM correlation), so it can
# span from sub-second up to a few minutes when the LLM is slow.
DURATION_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

# Histogram buckets for individual LangGraph node duration in seconds.
# Nodes are either fast in-memory operations (<1s) or HTTP/LLM calls (up to ~120s).
NODE_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)


class CorrelationMetrics:
    """Metrics for enigmatologist correlation instrumentation.

    Uses histograms and labeled counters rather than raw match counts to
    remain meaningful even when the same incident is processed multiple times.

    Usage:
        metrics = CorrelationMetrics(meter, "enigmatologist", "reliability", "incident")

        metrics.record_candidates_evaluated(
            github_count=12,
            jira_count=5,
        )
        metrics.record_run_outcome(
            outcome="llm_only",
            service_match_count=0,
            llm_match_count=2,
        )
        metrics.record_match_scores(
            scores=[0.75, 0.82],
            correlation_type="LLM",
        )
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
        workflow_type: str,
        anchor: str,
    ) -> None:
        """Initialize correlation metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service (e.g., "enigmatologist")
            workflow_type: Correlation workflow type (e.g., "reliability")
            anchor: Anchor entity type (e.g., "incident")
        """
        self._service_name = service_name
        self._workflow_type = workflow_type
        self._anchor = anchor

        self._candidates_evaluated = meter.create_histogram(
            name="matik_correlation_candidate_events",
            description="Number of change events evaluated as candidates per correlation run",
            unit="{event}",
            explicit_bucket_boundaries_advisory=COUNT_BUCKETS,
        )

        self._matches_per_run = meter.create_histogram(
            name="matik_correlation_run_matches",
            description="Number of matches found per correlation run",
            unit="{match}",
            explicit_bucket_boundaries_advisory=COUNT_BUCKETS,
        )

        self._match_score = meter.create_histogram(
            name="matik_correlation_score",
            description="Distribution of final correlation scores across matches",
            unit="1",
            explicit_bucket_boundaries_advisory=SCORE_BUCKETS,
        )

        self._run_outcomes = meter.create_counter(
            name="matik_correlation_outcomes_total",
            description="Correlation run outcomes by path: service_only, llm_only, both, none",
            unit="{run}",
        )

        self._run_duration = meter.create_histogram(
            name="matik_correlation_duration_seconds",
            description="End-to-end duration of a single correlation run in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        )

        self._node_duration = meter.create_histogram(
            name="matik_correlation_node_duration_seconds",
            description="Duration of an individual LangGraph pipeline node in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=NODE_DURATION_BUCKETS,
        )

    def record_candidates_evaluated(
        self,
        github_count: int,
        jira_count: int,
    ) -> None:
        """Record the number of change event candidates evaluated in a run.

        Both sources share the same metric name, differentiated by the
        'source' label — query as one metric, filter/group by source.

        Args:
            github_count: Number of GitHub PR candidates fetched
            jira_count: Number of Jira TCMR candidates fetched
        """
        self._candidates_evaluated.record(
            github_count,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
                "source": "biztech_github",
            },
        )
        self._candidates_evaluated.record(
            jira_count,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
                "source": "jira",
            },
        )

    def record_run_outcome(
        self,
        outcome: str,
        service_match_count: int,
        llm_match_count: int,
    ) -> None:
        """Record the outcome of a single correlation run.

        Args:
            outcome: One of "service_only", "llm_only", "both", "none"
            service_match_count: Number of service-based matches found
            llm_match_count: Number of LLM-based matches found
        """
        self._run_outcomes.add(
            1,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
                "outcome": outcome,
            },
        )

        total_matches = service_match_count + llm_match_count
        self._matches_per_run.record(
            total_matches,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
            },
        )

    def record_match_scores(
        self,
        scores: list[float],
        correlation_type: str,
    ) -> None:
        """Record final scores for matched correlations.

        Args:
            scores: List of final_score values from matched correlations
            correlation_type: "SERVICE_MATCH" or "LLM"
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "type": self._workflow_type,
            "anchor": self._anchor,
            "correlation_type": correlation_type,
        }
        for score in scores:
            self._match_score.record(score, attrs)

    def record_run_duration(
        self, duration_seconds: float, outcome: str = "unknown"
    ) -> None:
        """Record the wall-clock duration of a single correlation run.

        Args:
            duration_seconds: Time taken to execute the correlation pipeline.
            outcome: Run outcome — one of "service_only", "llm_only", "both", "none",
                     "error", or "unknown". Allows filtering fast no-match runs from
                     slow LLM runs in P95 latency panels.
        """
        self._run_duration.record(
            duration_seconds,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
                "outcome": outcome,
            },
        )

    def start_run(self) -> Callable[[str], None]:
        """Start timing a correlation run and return a function to record completion.

        Usage:
            record_duration = metrics.start_run()
            try:
                await graph.ainvoke(...)
            except Exception:
                record_duration("error")
                raise
            # ... compute outcome ...
            record_duration(outcome)

        Returns:
            A function that accepts an outcome string and records the elapsed duration.
        """
        start = time.perf_counter()

        def record(outcome: str = "unknown") -> None:
            self.record_run_duration(time.perf_counter() - start, outcome)

        return record

    def record_node_duration(self, node: str, duration_seconds: float) -> None:
        """Record the wall-clock duration of a single LangGraph pipeline node.

        Args:
            node: Node name (e.g., "fetch_jira_issues", "assign_correlations_by_llm").
            duration_seconds: Time taken to execute the node.
        """
        self._node_duration.record(
            duration_seconds,
            {
                "service": self._service_name,
                "type": self._workflow_type,
                "anchor": self._anchor,
                "node": node,
            },
        )

    def start_node(self, node: str) -> Callable[[], None]:
        """Start timing a LangGraph node and return a function to record completion.

        Usage:
            record_node = metrics.start_node("fetch_jira_issues")
            try:
                # ... node logic ...
            finally:
                record_node()

        Args:
            node: Node name for the label.

        Returns:
            A function that records the elapsed duration when called.
        """
        start = time.perf_counter()

        def record() -> None:
            self.record_node_duration(node, time.perf_counter() - start)

        return record
