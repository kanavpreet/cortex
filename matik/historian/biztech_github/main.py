"""Entry point for the GHE PR Crawler service.

Thin wiring over the shared ``run_historian`` shell: the ``build_and_run``
callback builds the GHE clients / publishers / metrics from the shell's
:class:`~historian.base.runner.HistorianContext` and runs the crawler. The common
shell (config merges, logging, Telescope, validation, shutdown) lives in
``historian.base.runner``.
"""

import sys

import boto3

from common.clients.artifactory_client import create_artifactory_client
from common.clients.ghe_client import create_ghe_client
from common.clients.matik_api_client import create_matik_api_client
from common.clients.sqs_publisher import SQSPublisher
from common.datasources.registry import get_source
from common.metrics import (
    ClientMetrics,
    GHECacheMetrics,
    JobMetrics,
    SQSPublisherMetrics,
)
from common.queues.enrichment_publisher import EnrichmentPublisher
from common.queues.sqs_client import SQSClient
from common.utils import log_utils
from historian.base.runner import HistorianContext, run_historian
from historian.biztech_github.ghe_pr_crawler import GHEPRCrawler

logger = log_utils.get_logger(__name__)


def _build_and_run(ctx: HistorianContext) -> int:
    """Build the GHE clients + publishers and run the crawler."""
    config = ctx.config
    github_config = config.biztech_github

    ghe_client_metrics: ClientMetrics | None = None
    cache_metrics: GHECacheMetrics | None = None
    job_metrics: JobMetrics | None = None
    sqs_publisher_metrics: SQSPublisherMetrics | None = None
    if ctx.meter is not None and ctx.service_name is not None:
        ghe_client_metrics = ClientMetrics(ctx.meter, ctx.service_name, "ghe")
        cache_metrics = GHECacheMetrics(ctx.meter, ctx.service_name)
        job_metrics = JobMetrics(ctx.meter, ctx.service_name)
        sqs_publisher_metrics = SQSPublisherMetrics(
            ctx.meter, config.telescope.service_name
        )

    # Validate required SQS configuration.
    if github_config.sqs_queue_url is None:
        logger.error("biztech_github.sqs_queue_url configuration is required")
        return 1

    # Enrichment publisher (async; the Enricher SQS queue is the sole LLM path).
    logger.info("Creating enricher SQS publisher")
    enricher_sqs_client = SQSClient(region=config.enricher.region)
    enrichment_publisher = EnrichmentPublisher(
        enricher_sqs_client, queue_url=config.enricher.enricher_queue_url
    )

    # GHE client (async) — enrichment is published inside the client per-PR.
    ghe_client = create_ghe_client(
        ghe_config=github_config,
        client_metrics=ghe_client_metrics,
        cache_metrics=cache_metrics,
        enrichment_publisher=enrichment_publisher,
    )

    # Matik API client (org/repo writes + reads).
    matik_client = create_matik_api_client(api_config=config.api)

    # SQS publisher (PR + tracker writes via Scribe).
    sqs_session = boto3.Session(region_name=github_config.sqs_queue_region)
    sqs_client = sqs_session.client("sqs")
    sqs_publisher = SQSPublisher(
        sqs_client, github_config.sqs_queue_url, sqs_publisher_metrics
    )

    artifactory_client = None
    if config.artifactory:
        artifactory_client = create_artifactory_client(config.artifactory)

    crawler = GHEPRCrawler(
        ghe_client=ghe_client,
        matik_client=matik_client,
        sqs_publisher=sqs_publisher,
        artifactory_client=artifactory_client,
        config=github_config,
        job_metrics=job_metrics,
        cache_metrics=cache_metrics,
    )

    # dispatch() owns the async event loop + GHE client teardown, and returns a
    # CrawlerResult (the shared historian contract). GHE records job metrics
    # inside run(), so we do not start a job here.
    result = crawler.dispatch()

    if result.success:
        logger.info(
            "GHE PR crawler completed successfully",
            prs_processed=result.records_processed,
        )
        return 0

    logger.error("GHE PR crawler failed", error=result.error_message)
    return 1


def main() -> int:
    """Entry point.

    Returns:
        0 on success, non-zero on failure.
    """
    return run_historian(
        spec=get_source("ghe_pr"),
        caller_name=__name__,
        source_config_files=[
            ("matik-api-config.yml", "Failed to load api config"),
            ("matik-historian-biztech-github-config.yml", "Failed to load config"),
            ("metrics.yml", "Failed to load metrics config"),
            ("matik-enricher-config.yml", "Failed to load enricher config"),
        ],
        build_and_run=_build_and_run,
        source_config_attr="biztech_github",
        source_config_missing_msg="biztech_github configuration is required",
        crash_log_msg="Crawler failed",
    )


if __name__ == "__main__":
    sys.exit(main())
