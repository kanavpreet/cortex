"""Jira crawler metrics for the historian Jira crawler.

Provides metrics for per-run crawler statistics (issues processed, Facade
API calls made).

Note: hash-based issue dedup (and its cache hit/miss/new accounting) moved
into the Enricher service; see ``matik_enricher_enrichment_cache_operations_total``
in :mod:`common.metrics.enricher_metrics`.
"""

from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class JiraCacheMetrics:
    """Metrics for the Jira crawler.

    Tracks per-run crawler statistics. Cache hit/miss accounting was removed
    when hash-based dedup moved to the Enricher.

    Usage:
        metrics = JiraCacheMetrics(meter, "historian")

        # Record crawler run statistics
        metrics.record_crawler_stats(
            issues_processed=50,
            facade_calls=15,
        )
    """

    def __init__(self, meter: metrics.Meter, service_name: str) -> None:
        """Initialize Jira crawler metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments.
            service_name: Name of the service using this client.
        """
        self._service_name = service_name

        # Crawler statistics counters
        self._issues_processed = meter.create_counter(
            name="matik_jira_crawler_issues_processed_total",
            description="Issues processed by Jira crawler",
            unit="{issue}",
        )

        self._facade_calls = meter.create_counter(
            name="matik_jira_crawler_facade_calls_total",
            description="Facade API calls made by Jira crawler",
            unit="{call}",
        )

    def record_crawler_stats(
        self,
        issues_processed: int,
        facade_calls: int,
    ) -> None:
        """Export crawler run statistics to Telescope.

        Called at the end of a crawler run to export aggregated statistics.

        Args:
            issues_processed: Number of issues processed.
            facade_calls: Number of Facade API calls made.
        """
        base_attrs: dict[str, Any] = {
            "service": self._service_name,
            "connector_type": "jira_issues",
        }

        if issues_processed > 0:
            self._issues_processed.add(issues_processed, base_attrs)
        if facade_calls > 0:
            self._facade_calls.add(facade_calls, base_attrs)

        logger.debug(
            "recorded jira crawler stats metric",
            issues_processed=issues_processed,
            facade_calls=facade_calls,
        )
