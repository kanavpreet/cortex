"""IncidentIO Historian service entry point.

Thin wiring over the shared ``run_historian`` shell: the ``build_and_run``
callback builds the Incident.io clients / publishers / metrics from the shell's
:class:`~historian.base.runner.HistorianContext` and runs the crawler. The common
shell (config merges, logging, Telescope, validation, shutdown) lives in
``historian.base.runner``.
"""

import sys

import boto3

from common.clients.incidentio_client import create_incidentio_client
from common.clients.matik_api_client import create_matik_api_client
from common.clients.sqs_publisher import SQSPublisher
from common.datasources.registry import get_source
from common.metrics import ClientMetrics, JobMetrics, SQSPublisherMetrics
from common.queues.enrichment_publisher import EnrichmentPublisher
from common.queues.sqs_client import SQSClient
from common.utils import log_utils
from historian.base.runner import HistorianContext, run_historian

from .incidentio_incident_crawler import IncidentIOIncidentCrawler

logger = log_utils.get_logger(__name__)

JOB_NAME = "incidentio_incidents"


def _build_and_run(ctx: HistorianContext) -> int:
    """Build the Incident.io clients + publishers and run the crawler."""
    config = ctx.config

    client_metrics: ClientMetrics | None = None
    job_metrics: JobMetrics | None = None
    sqs_publisher_metrics: SQSPublisherMetrics | None = None
    if ctx.meter is not None and ctx.service_name is not None:
        client_metrics = ClientMetrics(ctx.meter, ctx.service_name, "incidentio")
        job_metrics = JobMetrics(ctx.meter, ctx.service_name)
        sqs_publisher_metrics = SQSPublisherMetrics(
            ctx.meter, config.telescope.service_name
        )

    # Clients.
    matik_client = create_matik_api_client(api_config=config.api)
    incidentio_client = create_incidentio_client(
        incidentio_config=config.incidentio, client_metrics=client_metrics
    )

    # Validate required SQS configuration.
    if not config.incidentio.sqs_queue_url:
        logger.error("incidentio.sqs_queue_url configuration is required")
        return 1

    # Base-record SQS publisher.
    sqs_session = boto3.Session(region_name=config.incidentio.sqs_queue_region)
    sqs_client = sqs_session.client("sqs")
    sqs_publisher = SQSPublisher(
        sqs_client, config.incidentio.sqs_queue_url, sqs_publisher_metrics
    )
    logger.info("SQS publisher initialized", queue_url=config.incidentio.sqs_queue_url)

    # Enrichment publisher (async; enricher config validated by the shell).
    logger.info("Creating enrichment SQS publisher")
    enricher_sqs_client = SQSClient(region=config.enricher.region)
    enrichment_publisher = EnrichmentPublisher(
        enricher_sqs_client, queue_url=config.enricher.enricher_queue_url
    )

    record_job = job_metrics.start_job(JOB_NAME) if job_metrics else None

    crawler = IncidentIOIncidentCrawler(
        incidentio_config=config.incidentio,
        matik_client=matik_client,
        incidentio_client=incidentio_client,
        job_metrics=job_metrics,
        sqs_publisher=sqs_publisher,
        enrichment_publisher=enrichment_publisher,
    )

    try:
        result = crawler.dispatch()
    except Exception as e:
        # Record the job failure before letting the shell log the crash + return 1.
        if record_job:
            try:
                record_job(0, e)
            except Exception:
                logger.warning("Failed to record job metrics")
        raise

    if result.success:
        logger.info(
            "IncidentIO historian crawler completed successfully",
            records_processed=result.records_processed,
        )
        if record_job:
            try:
                record_job(result.records_processed, None)
            except Exception:
                logger.warning("Failed to record job metrics")
        return 0

    logger.error(
        "IncidentIO historian crawler failed",
        error=result.error_message,
    )
    if record_job:
        try:
            record_job(result.records_processed, Exception(result.error_message))
        except Exception:
            logger.warning("Failed to record job metrics")
    return 1


def main() -> int:
    """Run the IncidentIO historian crawler.

    Returns:
        0 on success, non-zero on failure.
    """
    return run_historian(
        spec=get_source("incidentio"),
        caller_name=__name__,
        source_config_files=[
            ("matik-historian-incidentio-config.yml", "Failed to load config"),
            ("metrics.yml", "Failed to load metrics config"),
            ("matik-enricher-config.yml", "Failed to load enricher config"),
        ],
        build_and_run=_build_and_run,
        source_config_missing_msg="IncidentIO configuration not found",
        crash_log_msg="IncidentIO historian crawler failed with unexpected error",
    )


if __name__ == "__main__":  # pragma: no cover - entry point guard
    sys.exit(main())
