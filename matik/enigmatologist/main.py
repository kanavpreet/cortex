"""Enigmatologist service - SQS-based correlation worker."""

import asyncio
import json
from typing import Any

import boto3
import httpx

from common.clients import create_bedrock_client, create_facade_client
from common.config import load_config, merge_config
from common.llm_tracing.client import LLMTracingClient
from common.metrics import (
    ClientMetrics,
    CorrelationMetrics,
    FacadeMetrics,
    SQSMetrics,
    SQSPublisherMetrics,
)
from common.metrics.telescope import TelescopeClient
from common.models.matik_config import MatikConfig
from common.utils import log_utils
from common.utils.env_utils import extract_full_service, extract_service

from .correlations.reliability.incident.main import (
    run_correlation as run_reliability_correlation,
)

logger = log_utils.get_logger(__name__)


def _create_sqs_client(region: str) -> Any:
    """Create an SQS client using default boto3 credentials."""
    session = boto3.Session(region_name=region)
    return session.client("sqs")


async def _poll_reliability_sqs(
    sqs_client: Any,
    queue_url: str,
    semaphore: asyncio.Semaphore,
    llm_client: Any,
    api_client: httpx.AsyncClient | None,
    config: MatikConfig,
    sqs_metrics: SQSMetrics | None = None,
    correlation_metrics: CorrelationMetrics | None = None,
    publisher_metrics: SQSPublisherMetrics | None = None,
) -> None:
    """Long-poll SQS for reliability correlation requests and process them concurrently."""
    assert config.enigmatologist is not None
    # Strong references to background tasks to prevent GC
    background_tasks: set[asyncio.Task[None]] = set()

    while True:
        try:
            response = await asyncio.to_thread(
                sqs_client.receive_message,
                QueueUrl=queue_url,
                MaxNumberOfMessages=config.enigmatologist.sqs_max_messages,
                WaitTimeSeconds=config.enigmatologist.sqs_wait_time_seconds,
                VisibilityTimeout=config.enigmatologist.sqs_visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"],
            )

            messages = response.get("Messages", [])
            queue_name = queue_url.split("/")[-1]
            if sqs_metrics:
                sqs_metrics.record_poll(queue_name)

            if not messages:
                continue

            for message in messages:
                if sqs_metrics:
                    receive_count = int(
                        message.get("Attributes", {}).get(
                            "ApproximateReceiveCount", "1"
                        )
                    )
                    sqs_metrics.record_retry(queue_name, receive_count)

                try:
                    payload = json.loads(message["Body"])
                except (json.JSONDecodeError, KeyError):
                    logger.warning(
                        "invalid SQS message, deleting",
                        receipt_handle=message.get("ReceiptHandle"),
                    )
                    await asyncio.to_thread(
                        sqs_client.delete_message,
                        QueueUrl=queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
                    continue

                receipt_handle = message["ReceiptHandle"]

                async def _process(
                    payload: dict[str, Any] = payload,
                    receipt_handle: str = receipt_handle,
                    queue_name: str = queue_name,
                ) -> None:
                    async with semaphore:
                        record_msg = (
                            sqs_metrics.start_message(queue_name)
                            if sqs_metrics
                            else None
                        )
                        try:
                            await run_reliability_correlation(
                                payload,
                                llm_client,
                                api_client,
                                config,
                                sqs_client=sqs_client,
                                correlation_metrics=correlation_metrics,
                                publisher_metrics=publisher_metrics,
                            )
                            await asyncio.to_thread(
                                sqs_client.delete_message,
                                QueueUrl=queue_url,
                                ReceiptHandle=receipt_handle,
                            )
                            if record_msg:
                                record_msg(True, None)
                        except Exception as err:
                            if record_msg:
                                record_msg(False, err)
                            logger.warning(
                                "correlation failed, message will be retried",
                                reference_id=payload.get("reference_id"),
                                exc_info=True,
                            )

                task = asyncio.create_task(_process())
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)

        except Exception:
            logger.warning("SQS poll error, retrying", exc_info=True)
            await asyncio.sleep(config.enigmatologist.sqs_poll_error_delay)


async def main() -> int:
    """Run the enigmatologist SQS polling worker.

    Returns:
        0 on success, non-zero on failure.
    """
    try:
        config = load_config("matik-enigmatologist-config.yml")
    except Exception:
        log_utils.configure(level="INFO")
        logger.exception("Failed to load enigmatologist config")
        return 1

    log_utils.configure(level="INFO", environment=config.common.environment)
    logger.info("starting enigmatologist")

    try:
        config = merge_config(config, "metrics.yml")
    except FileNotFoundError:
        logger.warning("metrics.yml not found, proceeding without metrics config")

    try:
        config = merge_config(config, "llm-tracing-config.yml")
    except FileNotFoundError:
        logger.warning(
            "llm-tracing-config.yml not found, proceeding without LLM tracing"
        )

    llm_provider = (
        config.enigmatologist.llm_provider if config.enigmatologist else "facade"
    )
    logger.info("llm_provider configured", llm_provider=llm_provider)

    # Initialize Telescope metrics if configured (before client creation so metrics
    # can be passed to the clients)
    telescope: TelescopeClient | None = None
    llm_client_metrics: ClientMetrics | None = None
    facade_metrics: FacadeMetrics | None = None
    sqs_metrics: SQSMetrics | None = None
    correlation_metrics: CorrelationMetrics | None = None
    publisher_metrics: SQSPublisherMetrics | None = None

    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = extract_full_service(__name__)
        config.telescope.environment = config.common.environment

        telescope = TelescopeClient(config.telescope)
        telescope.start()
        meter = telescope.meter
        service_name = extract_service(__name__)

        llm_client_metrics = ClientMetrics(meter, service_name, llm_provider)
        facade_metrics = FacadeMetrics(
            meter,
            config.telescope.service_name,
            llm_provider,
            track_rate_limits=False,
        )
        sqs_metrics = SQSMetrics(meter, service_name)
        correlation_metrics = CorrelationMetrics(
            meter, service_name, "reliability", "incident"
        )
        publisher_metrics = SQSPublisherMetrics(meter, service_name)

        logger.info("Telescope metrics initialized")

    # Initialize LLM call tracing (Facade + Bedrock calls -> Braintrust)
    tracing: LLMTracingClient | None = None
    if config.llm_tracing and config.llm_tracing.enabled:
        config.llm_tracing.environment = config.common.environment
        config.llm_tracing.service_name = extract_service(__name__)
        tracing = LLMTracingClient(config.llm_tracing)
        tracing.start()

    llm_client: Any = None
    if llm_provider == "bedrock":
        config = merge_config(config, "bedrock-config.yml")
        if config.bedrock:
            llm_client = create_bedrock_client(
                bedrock_config=config.bedrock,
                common_config=config.common,
                metrics=llm_client_metrics,
                facade_metrics=facade_metrics,
            )
        else:
            logger.error("bedrock config is not loaded")
    else:
        config = merge_config(config, "facade-config.yml")
        if config.facade:
            llm_client = create_facade_client(
                facade_config=config.facade,
                common_config=config.common,
                metrics=llm_client_metrics,
                facade_metrics=facade_metrics,
            )
        else:
            logger.error("facade config is not loaded")

    # Initialize SQS client
    if not config.enigmatologist or not config.enigmatologist.sqs_queue_url:
        logger.error("enigmatologist sqs_queue_url not configured")
        return 1

    if not config.enigmatologist.scribe_queue_url:
        logger.error("enigmatologist scribe_queue_url not configured")
        return 1

    # Configure API client for persisting results
    api_client: httpx.AsyncClient | None = None
    if config.api:
        api_client = httpx.AsyncClient(base_url=config.api.api_endpoint)
        logger.info("api configured for persistence", url=config.api.api_endpoint)
    else:
        logger.warning("api config not found, persistence disabled")

    queue_url = config.enigmatologist.sqs_queue_url
    aws_region = config.enigmatologist.region
    sqs_client = _create_sqs_client(aws_region)

    logger.info(
        "SQS polling started",
        queue_url=queue_url,
        aws_region=aws_region,
        max_concurrent_correlations=config.enigmatologist.max_concurrent_correlations,
    )

    semaphore = asyncio.Semaphore(config.enigmatologist.max_concurrent_correlations)

    try:
        await _poll_reliability_sqs(
            sqs_client,
            queue_url,
            semaphore,
            llm_client,
            api_client,
            config,
            sqs_metrics=sqs_metrics,
            correlation_metrics=correlation_metrics,
            publisher_metrics=publisher_metrics,
        )
    finally:
        if api_client:
            await api_client.aclose()
        if telescope:
            telescope.shutdown()
            logger.info("Telescope client shut down")
        if tracing:
            tracing.shutdown()

    return 0
