"""GHE crawler metrics for the historian GHE PR crawler.

Provides metrics for tracking GitHub rate-limit events and per-run crawler
statistics (orgs/repos/PRs processed, API calls made).

Note: hash-based PR description dedup (and its cache hit/miss/new accounting)
moved into the Enricher service; see ``matik_enricher_enrichment_cache_operations_total``
in :mod:`common.metrics.enricher_metrics`.
"""

from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class GHECacheMetrics:
    """Metrics for the GHE PR crawler.

    Tracks GitHub rate-limit events and per-run crawler statistics. Cache
    hit/miss accounting was removed when hash-based dedup moved to the Enricher.

    Usage:
        metrics = GHECacheMetrics(meter, "historian")

        # Record rate limit events
        metrics.record_rate_limit("get_prs")

        # Record crawler run statistics
        metrics.record_crawler_stats(
            orgs_processed=2,
            repos_processed=10,
            prs_upserted=50,
            api_calls_ghe=15,
            api_calls_matik=30,
        )
    """

    def __init__(self, meter: metrics.Meter, service_name: str) -> None:
        """Initialize GHE crawler metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments.
            service_name: Name of the service using this client.
        """
        self._service_name = service_name

        self._rate_limit_events = meter.create_counter(
            name="matik_ghe_rate_limit_events_total",
            description="GitHub rate limit events encountered",
            unit="{event}",
        )

        # Crawler statistics counters
        self._orgs_processed = meter.create_counter(
            name="matik_ghe_crawler_orgs_processed_total",
            description="Organizations processed by GHE crawler",
            unit="{org}",
        )

        self._repos_processed = meter.create_counter(
            name="matik_ghe_crawler_repos_processed_total",
            description="Repositories processed by GHE crawler",
            unit="{repo}",
        )

        self._prs_upserted = meter.create_counter(
            name="matik_ghe_crawler_prs_upserted_total",
            description="PRs upserted to database by GHE crawler",
            unit="{pr}",
        )

        self._api_calls = meter.create_counter(
            name="matik_ghe_crawler_api_calls_total",
            description="API calls made by GHE crawler",
            unit="{call}",
        )

    def record_rate_limit(self, operation: str) -> None:
        """Record a GitHub rate limit event.

        Args:
            operation: Name of the operation that hit rate limit.
        """
        attrs: dict[str, Any] = {
            "service": self._service_name,
            "operation": operation,
        }
        self._rate_limit_events.add(1, attrs)
        logger.debug(
            "recorded ghe rate limit metric",
            operation=operation,
        )

    def record_crawler_stats(
        self,
        orgs_processed: int,
        repos_processed: int,
        prs_upserted: int,
        api_calls_ghe: int,
        api_calls_matik: int,
    ) -> None:
        """Export crawler run statistics to Telescope.

        Called at the end of a crawler run to export aggregated statistics.

        Args:
            orgs_processed: Number of organizations processed.
            repos_processed: Number of repositories processed.
            prs_upserted: Number of PRs upserted to the database.
            api_calls_ghe: Number of API calls made to GitHub Enterprise.
            api_calls_matik: Number of API calls made to Matik API.
        """
        base_attrs: dict[str, Any] = {
            "service": self._service_name,
            "connector_type": "ghe_pr",
        }

        if orgs_processed > 0:
            self._orgs_processed.add(orgs_processed, base_attrs)
        if repos_processed > 0:
            self._repos_processed.add(repos_processed, base_attrs)
        if prs_upserted > 0:
            self._prs_upserted.add(prs_upserted, base_attrs)
        if api_calls_ghe > 0:
            self._api_calls.add(api_calls_ghe, {**base_attrs, "target": "ghe"})
        if api_calls_matik > 0:
            self._api_calls.add(api_calls_matik, {**base_attrs, "target": "matik"})

        logger.debug(
            "recorded ghe crawler stats metric",
            orgs_processed=orgs_processed,
            repos_processed=repos_processed,
            prs_upserted=prs_upserted,
            api_calls_ghe=api_calls_ghe,
            api_calls_matik=api_calls_matik,
        )
