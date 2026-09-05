"""Chronicler service entry point — long-running Kafka consumer loop.

Subscribes to Yoyo callback topics directly via confluent-kafka-python and
routes events through the provider transformer registry to Scribe and
Enricher SQS queues.
"""

import asyncio
from contextlib import suppress

import boto3

from chronicler.consumer import (
    KafkaConsumerLoop,
    build_consumer,
    install_signal_handlers,
)
from chronicler.transformers.base import get_transformer, register_transformer
from chronicler.transformers.github_pr import GitHubPRTransformer
from common.clients.ghe_client import GHEClient, create_ghe_client
from common.clients.sqs_publisher import SQSPublisher
from common.config import load_config, merge_config
from common.metrics.chronicler_metrics import ChroniclerMetrics
from common.metrics.sqs_publisher_metrics import SQSPublisherMetrics
from common.metrics.telescope import TelescopeClient
from common.models.matik_config import MatikConfig
from common.queues.enrichment_publisher import EnrichmentPublisher
from common.queues.sqs_client import SQSClient
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def _load_config() -> MatikConfig:
    """Load and merge configuration files."""
    config = load_config("matik-chronicler-config.yml")
    with suppress(FileNotFoundError):
        config = merge_config(config, "metrics.yml")
    return config


async def _run() -> None:
    config = _load_config()
    log_utils.configure(
        level=config.common.log_level, environment=config.common.environment
    )

    chronicler_cfg = config.chronicler
    if chronicler_cfg is None:
        logger.error("chronicler config not found")
        raise RuntimeError("chronicler config section is required")

    # Fail-fast: verify expected providers are registered.
    for provider_name in ("github", "jira", "incidentio", "matik"):
        get_transformer(provider_name)
    logger.info("Provider transformers verified")

    # Webhook payloads omit PR comments, so give the github transformer a
    # read-only GHE client to fetch the PR conversation thread at webhook time.
    # Any misconfiguration (missing creds/key) degrades to description-only
    # enrichment — never crashes startup — and the historian crawl folds in
    # comments on its next pass.
    ghe_client: GHEClient | None = None
    gh_cfg = config.biztech_github
    if gh_cfg is not None and gh_cfg.ghe_app_id:
        try:
            ghe_client = create_ghe_client(ghe_config=gh_cfg)
            register_transformer("github", GitHubPRTransformer(ghe_client=ghe_client))
            logger.info("GHE comment client wired into github transformer")
        except Exception:
            ghe_client = None
            logger.warning(
                "Failed to initialize GHE comment client — GHE PR enrichment "
                "will use the description only",
                exc_info=True,
            )
    else:
        logger.warning(
            "biztech_github config not set — GHE PR enrichment will use the "
            "description only (no conversation comments)"
        )

    if not chronicler_cfg.kafka.bootstrap_servers:
        raise RuntimeError("chronicler.kafka.bootstrap_servers is required")
    if not chronicler_cfg.scribe_queue_url:
        raise RuntimeError("chronicler.scribe_queue_url is required")

    # Telescope metrics. Must be initialized before publishers so the SQS
    # publisher can take a meter-backed SQSPublisherMetrics; the consumer loop
    # below also takes ChroniclerMetrics from the same meter.
    telescope: TelescopeClient | None = None
    chronicler_metrics: ChroniclerMetrics | None = None
    publisher_metrics: SQSPublisherMetrics | None = None
    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = "chronicler"
        config.telescope.environment = config.common.environment
        telescope = TelescopeClient(config.telescope)
        telescope.start()
        meter = telescope.meter
        chronicler_metrics = ChroniclerMetrics(meter, "chronicler")
        publisher_metrics = SQSPublisherMetrics(meter, "chronicler")
        logger.info("Telescope metrics initialized")

    # Scribe SQS publisher (sync boto3 client wrapped by SQSPublisher).
    scribe_session = boto3.Session(region_name=chronicler_cfg.scribe_queue_region)
    scribe_sqs_client = scribe_session.client("sqs")
    scribe_publisher = SQSPublisher(
        sqs_client=scribe_sqs_client,
        queue_url=chronicler_cfg.scribe_queue_url,
        metrics=publisher_metrics,
    )
    logger.info(
        "Scribe publisher initialized",
        queue_url=chronicler_cfg.scribe_queue_url,
    )

    # Enricher SQS publisher (async wrapper).
    enrichment_publisher: EnrichmentPublisher | None = None
    if chronicler_cfg.enricher_queue_url:
        enricher_sqs = SQSClient(region=chronicler_cfg.resolved_enricher_region)
        enrichment_publisher = EnrichmentPublisher(
            sqs_client=enricher_sqs,
            queue_url=chronicler_cfg.enricher_queue_url,
        )
        logger.info(
            "Enrichment publisher initialized",
            queue_url=chronicler_cfg.enricher_queue_url,
        )
    else:
        logger.warning("Enricher queue not configured, enrichment publishing disabled")

    if not chronicler_cfg.github_webhook_secret:
        logger.warning(
            "github_webhook_secret is not set — HMAC validation disabled for GHE events. "
            "Acceptable for sandbox bootstrap; staging/prod must set this in secret-lair."
        )
    if not chronicler_cfg.incidentio_webhook_secret:
        logger.warning(
            "incidentio_webhook_secret is not set — Svix signature validation "
            "disabled for Incident.io events. Acceptable for sandbox bootstrap; "
            "staging/prod must set this in secret-lair."
        )
    if not chronicler_cfg.generic_webhook_secret:
        logger.warning(
            "generic_webhook_secret is not set — HMAC validation disabled for "
            "the generic Matik webhook (e.g. OpsBot events). Acceptable for "
            "sandbox bootstrap; staging/prod must set this in secret-lair."
        )

    # Kafka consumer + loop.
    consumer = build_consumer(chronicler_cfg.kafka)
    loop = KafkaConsumerLoop(
        consumer=consumer,
        kafka_config=chronicler_cfg.kafka,
        scribe_publisher=scribe_publisher,
        enrichment_publisher=enrichment_publisher,
        chronicler_config=chronicler_cfg,
        metrics=chronicler_metrics,
    )
    install_signal_handlers(loop)

    logger.info("Chronicler service started")
    try:
        await loop.run()
    finally:
        if ghe_client is not None:
            await ghe_client.close()
        if telescope:
            telescope.shutdown()
            logger.info("Telescope client shut down")


def main() -> int:
    """Entry point: run the consumer loop until SIGTERM/SIGINT."""
    try:
        asyncio.run(_run())
        return 0
    except Exception:
        logger.exception("Chronicler exited with an unhandled error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
