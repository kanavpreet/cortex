"""Enricher service - consumer loop, error classes, backoff, and bootstrap."""

import asyncio
import json
import signal
from typing import Any

from common.clients.facade_client import FacadeBadRequestError
from common.config import load_config, merge_config
from common.llm_tracing import traced_llm_operation
from common.llm_tracing.client import LLMTracingClient
from common.metrics import EnricherMetrics, FacadeMetrics, SQSMetrics
from common.metrics.client_metrics import ClientMetrics
from common.metrics.telescope import TelescopeClient
from common.models.enricher_messages import EnrichmentRequest
from common.models.matik_config import MatikConfig
from common.utils import log_utils
from common.utils.env_utils import extract_full_service, extract_service
from common.utils.queue_utils import poll_queue_depth
from common.utils.retry_utils import (
    compute_backoff_with_jitter as _compute_backoff_with_jitter,
)
from enricher.exceptions import (
    ContentFilteredError,  # noqa: F401 - re-exported for consumers
    MalformedMessageError,  # noqa: F401 - re-exported for consumers
    NonRetryableError,
)

logger = log_utils.get_logger(__name__)

# Backoff tier delays (seconds) and jitter fraction
_BACKOFF_TIERS: list[float] = [10, 30, 60]
_JITTER_FRACTION = 0.2


def compute_backoff_with_jitter(receive_count: int) -> float:
    """Compute a backoff delay with jitter using the enricher's configured tiers.

    Args:
        receive_count: The SQS ApproximateReceiveCount for the message (1-based).

    Returns:
        Backoff duration in seconds with ±20% jitter applied.
    """
    return _compute_backoff_with_jitter(receive_count, _BACKOFF_TIERS, _JITTER_FRACTION)


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------


async def run_consumer_loop(
    config: MatikConfig,
    sqs_client: Any,
    processor: Any,
    enricher_metrics: EnricherMetrics | None,
    shutdown_event: asyncio.Event,
    handler: Any | None = None,
    sqs_metrics: SQSMetrics | None = None,
) -> None:
    """Main SQS consumer loop: poll -> pre-parse -> batch-fetch -> process -> classify outcome.

    Runs until shutdown_event is set, then drains in-flight tasks.

    After each SQS poll, pre-parses all messages and batch-fetches hashes for
    parseable requests (one API call per source_type group). Pre-parsed messages
    call handler.enrich() directly with the pre-fetched hashes, bypassing
    double-parsing. Unparseable messages fall back to processor.process() which
    handles MalformedMessageError -> DLQ.

    Message outcomes:
      - **Success**: delete message, record processed metric.
      - **NonRetryableError / FacadeBadRequestError / ContentFilteredError**:
        send to DLQ, delete original, record DLQ metric.
      - **Any other exception**: extend visibility with backoff, record backoff metric.

    Args:
        config: MatikConfig with config.enricher populated.
        sqs_client: Async SQSClient instance.
        processor: EnricherProcessor instance (used for unparseable messages).
        enricher_metrics: EnricherMetrics for recording outcomes (optional).
        shutdown_event: Set this event to trigger graceful shutdown.
        handler: EnrichmentHandler instance for the pre-parsed fast path.
            When None, all messages fall back to processor.process().
    """
    assert config.enricher is not None
    enricher_cfg = config.enricher
    semaphore = asyncio.Semaphore(enricher_cfg.max_concurrent_llm_calls)
    background_tasks: set[asyncio.Task[None]] = set()
    queue_name = enricher_cfg.enricher_queue_url.rstrip("/").rsplit("/", 1)[-1]

    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Discard the finished task and surface any unhandled exception.

        _process() catches its own processing errors, so a non-cancellation
        exception here means something escaped that handling entirely (e.g. a
        failure starting metrics or acquiring the semaphore). Without this the
        exception would be silently swallowed by the fire-and-forget task.
        """
        background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Enrichment task failed with an unhandled exception",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=exc,
            )

    # Cap the number of received-but-not-yet-finished messages held at once.
    # Each received message's SQS visibility timer starts at receive time, but
    # only ``max_concurrent_llm_calls`` are actually processed at a time. If we
    # keep receiving regardless, a burst floods us with messages whose timers
    # tick down while they wait for a processing slot. The backlog then exceeds
    # the visibility window, SQS redelivers, the redeliveries pile on more work,
    # and messages eventually exhaust maxReceiveCount and land in the DLQ — with
    # no error logged, since a visibility timeout never raises in-process.
    #
    # Bound in-flight work to the processing concurrency plus a single receive
    # batch. The batch buffers just enough work behind the semaphore that a
    # freed slot is filled immediately (avoiding one SQS receive call per
    # message at saturation), while keeping every received message starting
    # within ~1s — orders of magnitude under the visibility timeout.
    max_in_flight = max(enricher_cfg.max_concurrent_llm_calls, 1) + max(
        enricher_cfg.sqs_max_messages, 1
    )

    while not shutdown_event.is_set():
        # Backpressure: don't receive more than we have capacity to process.
        # Drop finished tasks first so capacity is measured accurately, then
        # wait for a slot if we are already at the in-flight limit.
        background_tasks.difference_update([t for t in background_tasks if t.done()])
        while len(background_tasks) >= max_in_flight and not shutdown_event.is_set():
            await asyncio.wait(background_tasks, return_when=asyncio.FIRST_COMPLETED)
            background_tasks.difference_update(
                [t for t in background_tasks if t.done()]
            )
        if shutdown_event.is_set():
            break

        try:
            messages = await sqs_client.receive_messages(
                queue_url=enricher_cfg.enricher_queue_url,
                max_messages=min(
                    enricher_cfg.sqs_max_messages,
                    max_in_flight - len(background_tasks),
                ),
                wait_time_seconds=enricher_cfg.sqs_wait_time_seconds,
                visibility_timeout=enricher_cfg.visibility_timeout_seconds,
            )
        except Exception:
            logger.warning("SQS poll error, retrying", exc_info=True)
            await asyncio.sleep(enricher_cfg.sqs_poll_error_delay)
            continue

        if sqs_metrics:
            sqs_metrics.record_poll(queue_name)

        if not messages:
            continue

        # Pre-parse all messages and batch-fetch hashes for parseable requests
        parsed: list[tuple[dict[str, Any], EnrichmentRequest | None]] = []
        for message in messages:
            body: str = message.get("Body", "")
            try:
                data = json.loads(body)
                request = EnrichmentRequest.model_validate(data)
                # Also validate source_type is configured
                if request.source_type not in enricher_cfg.source_mappings:
                    raise ValueError(
                        f"No mapping configured for source_type '{request.source_type}'"
                    )
                parsed.append((message, request))
            except Exception:
                parsed.append((message, None))  # let _process() handle the error

        parseable_requests = [req for _, req in parsed if req is not None]
        prefetched: dict[str, dict[str, Any]] = {}
        if parseable_requests and handler is not None:
            try:
                prefetched = await handler.batch_fetch_hashes(parseable_requests)
            except Exception:
                logger.warning(
                    "Batch hash fetch failed, will fetch individually", exc_info=True
                )
                prefetched = {}

        for message, request in parsed:  # type: ignore[assignment]
            receipt_handle: str = message["ReceiptHandle"]
            body = message.get("Body", "")
            receive_count = int(
                message.get("Attributes", {}).get("ApproximateReceiveCount", "1")
            )

            # Capture pre-fetched hashes for this specific request (if available)
            pre_hashes: dict[str, Any] | None = None
            if request is not None and handler is not None:
                canon_key = json.dumps(request.entity_id, sort_keys=True)
                pre_hashes = prefetched.get(canon_key)

            async def _process(
                body: str = body,
                receipt_handle: str = receipt_handle,
                receive_count: int = receive_count,
                request: EnrichmentRequest | None = request,
                pre_hashes: dict[str, Any] | None = pre_hashes,
            ) -> None:
                source_type = _extract_source_type(body)
                record = (
                    enricher_metrics.start_message(source_type)
                    if enricher_metrics
                    else None
                )
                with traced_llm_operation(
                    "enricher_process_message",
                    source=source_type,
                    entity_id=request.entity_id if request else None,
                    entity_created_at=request.entered_at if request else None,
                ):
                    async with semaphore:
                        try:
                            if request is not None and handler is not None:
                                # Fast path: already parsed, use pre-fetched hashes
                                await handler.enrich(
                                    request, existing_hashes=pre_hashes
                                )
                            else:
                                # Fallback path: processor handles parse errors -> DLQ
                                await processor.process(body)
                            await sqs_client.delete_message(
                                queue_url=enricher_cfg.enricher_queue_url,
                                receipt_handle=receipt_handle,
                            )
                            if record:
                                record(True, None)
                            logger.info(
                                "Enrichment message processed successfully",
                                source_type=source_type,
                            )

                        except (
                            NonRetryableError,
                            FacadeBadRequestError,
                        ) as err:
                            logger.warning(
                                "Non-retryable error, routing to DLQ",
                                error=str(err),
                                error_type=type(err).__name__,
                                source_type=source_type,
                                receive_count=receive_count,
                                exc_info=True,
                            )
                            try:
                                await sqs_client.send_message(
                                    queue_url=enricher_cfg.enricher_dlq_url,
                                    message_body=body,
                                )
                            except Exception:
                                logger.error(
                                    "Failed to send message to DLQ", exc_info=True
                                )
                            try:
                                await sqs_client.delete_message(
                                    queue_url=enricher_cfg.enricher_queue_url,
                                    receipt_handle=receipt_handle,
                                )
                            except Exception:
                                logger.error(
                                    "Failed to delete message after DLQ routing",
                                    exc_info=True,
                                )
                            if enricher_metrics:
                                enricher_metrics.record_dlq(source_type, err)
                            if record:
                                record(False, err)

                        except Exception as err:
                            # Final delivery attempt: SQS will redrive this message to
                            # the DLQ after the visibility timeout expires. SQS emits no
                            # log when it does so, so record a terminal log line and DLQ
                            # metric here — otherwise the message vanishes silently.
                            if receive_count >= enricher_cfg.max_receive_count:
                                logger.error(
                                    "Max receive count reached, SQS will redrive to DLQ",
                                    error=str(err),
                                    error_type=type(err).__name__,
                                    source_type=source_type,
                                    receive_count=receive_count,
                                    max_receive_count=enricher_cfg.max_receive_count,
                                    exc_info=True,
                                )
                                if enricher_metrics:
                                    enricher_metrics.record_dlq(source_type, err)
                                if record:
                                    record(False, err)
                                return

                            backoff = compute_backoff_with_jitter(receive_count)
                            logger.warning(
                                "Retryable error, applying backoff",
                                error=str(err),
                                error_type=type(err).__name__,
                                source_type=source_type,
                                backoff_seconds=backoff,
                                receive_count=receive_count,
                                exc_info=True,
                            )
                            try:
                                await sqs_client.change_message_visibility(
                                    queue_url=enricher_cfg.enricher_queue_url,
                                    receipt_handle=receipt_handle,
                                    visibility_timeout=int(backoff),
                                )
                            except Exception:
                                logger.error(
                                    "Failed to change message visibility",
                                    exc_info=True,
                                )
                            if enricher_metrics:
                                enricher_metrics.record_backoff(
                                    source_type, receive_count
                                )
                            if record:
                                record(False, err)

            task = asyncio.create_task(_process())
            background_tasks.add(task)
            task.add_done_callback(_on_task_done)

    # Drain in-flight tasks on shutdown
    if background_tasks:
        logger.info("Draining in-flight tasks", count=len(background_tasks))
        await asyncio.gather(*background_tasks, return_exceptions=True)


def _extract_source_type(message_body: str) -> str:
    """Extract the source_type from a raw SQS message body for metrics labeling.

    Returns "unknown" if the body cannot be parsed or source_type is missing.

    Args:
        message_body: Raw JSON string from the SQS message Body field.

    Returns:
        The source_type string, or "unknown" on any parse error.
    """
    try:
        data = json.loads(message_body)
        return str(data.get("source_type", "unknown"))
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def run_enricher(config: MatikConfig) -> int:
    """Bootstrap and run the Enricher service.

    Sets up Telescope metrics and LLM call tracing, creates all clients, and
    starts the consumer loop with a background queue-depth polling task.
    Registers SIGTERM/SIGINT handlers for graceful shutdown.

    Args:
        config: Fully-loaded MatikConfig with config.enricher populated.

    Returns:
        0 on clean shutdown, 1 on configuration error.
    """
    # Deferred imports to avoid circular imports at module level
    from common.clients.bedrock_client import create_bedrock_client
    from common.clients.facade_client import create_facade_client
    from common.queues.sqs_client import SQSClient
    from enricher.handlers.enrichment_handler import EnrichmentHandler
    from enricher.hash_cache import HashCache
    from enricher.processor import EnricherProcessor

    if config.enricher is None:
        logger.error("enricher config not found")
        return 1

    enricher_cfg = config.enricher
    llm_provider = enricher_cfg.llm_provider
    logger.info("llm_provider configured", llm_provider=llm_provider)

    missing = [
        name
        for name, value in (
            ("enricher_dlq_url", enricher_cfg.enricher_dlq_url),
            ("scribe_llm_queue_url", enricher_cfg.scribe_llm_queue_url),
            ("general_prompt", enricher_cfg.general_prompt),
            ("source_mappings", enricher_cfg.source_mappings),
        )
        if not value
    ]
    if missing:
        logger.error(
            "enricher config missing required fields for the enricher service",
            missing_fields=missing,
        )
        return 1

    # Initialize Telescope and metrics
    telescope: TelescopeClient | None = None
    facade_metrics: FacadeMetrics | None = None
    client_metrics: ClientMetrics | None = None
    enricher_metrics: EnricherMetrics | None = None
    sqs_metrics: SQSMetrics | None = None

    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = extract_full_service(__name__)
        config.telescope.environment = config.common.environment

        telescope = TelescopeClient(config.telescope)
        telescope.start()
        meter = telescope.meter
        service_name = extract_service(__name__)

        client_metrics = ClientMetrics(meter, service_name, llm_provider)
        facade_metrics = FacadeMetrics(
            meter, config.telescope.service_name, llm_provider
        )
        enricher_metrics = EnricherMetrics(meter, service_name)
        sqs_metrics = SQSMetrics(meter, service_name)

        logger.info("Telescope metrics initialized")

    # Initialize LLM call tracing (Facade + Bedrock calls -> Braintrust)
    tracing: LLMTracingClient | None = None
    if config.llm_tracing and config.llm_tracing.enabled:
        config.llm_tracing.environment = config.common.environment
        config.llm_tracing.service_name = extract_service(__name__)
        tracing = LLMTracingClient(config.llm_tracing)
        tracing.start()

    # Create the LLM client for the configured provider
    facade_client: Any
    if llm_provider == "bedrock":
        if config.bedrock is None:
            logger.error("bedrock config not found")
            if telescope:
                telescope.shutdown()
            if tracing:
                tracing.shutdown()
            return 1
        facade_client = create_bedrock_client(
            bedrock_config=config.bedrock,
            common_config=config.common,
            metrics=client_metrics,
            facade_metrics=facade_metrics,
        )
    else:
        if config.facade is None:
            logger.error("facade config not found")
            if telescope:
                telescope.shutdown()
            if tracing:
                tracing.shutdown()
            return 1
        facade_client = create_facade_client(
            facade_config=config.facade,
            common_config=config.common,
            metrics=client_metrics,
            facade_metrics=facade_metrics,
        )

    # Create Matik API client for hash fetching (optional)
    from common.clients.matik_api_client import create_matik_api_client

    matik_api_client = None
    if config.api is not None:
        matik_api_client = create_matik_api_client(api_config=config.api)

    # Create SQS client wrapper
    sqs_client = SQSClient(region=enricher_cfg.region)

    # Create hash cache
    hash_cache = HashCache(
        ttl_seconds=enricher_cfg.hash_cache_ttl_seconds,
        max_size=enricher_cfg.hash_cache_max_size,
    )

    # Create handler and processor
    handler = EnrichmentHandler(
        facade_client=facade_client,
        sqs_client=sqs_client,
        config=enricher_cfg,
        enricher_metrics=enricher_metrics,
        matik_api_client=matik_api_client,
        hash_cache=hash_cache,
    )
    processor = EnricherProcessor(handler=handler, config=enricher_cfg)

    # Graceful shutdown event
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_shutdown() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_shutdown)

    logger.info(
        "Enricher service started",
        queue_url=enricher_cfg.enricher_queue_url,
        region=enricher_cfg.region,
        max_concurrent_llm_calls=enricher_cfg.max_concurrent_llm_calls,
    )

    tasks: list[asyncio.Task[Any]] = []
    try:
        consumer_task = asyncio.create_task(
            run_consumer_loop(
                config=config,
                sqs_client=sqs_client,
                processor=processor,
                enricher_metrics=enricher_metrics,
                shutdown_event=shutdown_event,
                handler=handler,
                sqs_metrics=sqs_metrics,
            )
        )
        tasks.append(consumer_task)

        if enricher_metrics:
            depth_task = asyncio.create_task(
                poll_queue_depth(
                    sqs_client=sqs_client,
                    config=config,
                    enricher_metrics=enricher_metrics,
                    shutdown_event=shutdown_event,
                )
            )
            tasks.append(depth_task)

        await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        if telescope:
            telescope.shutdown()
            logger.info("Telescope client shut down")
        if tracing:
            tracing.shutdown()

    return 0


async def main() -> int:
    """Load config and run the Enricher service.

    Returns:
        0 on success, 1 on configuration or startup failure.
    """
    try:
        config = load_config("matik-enricher-config.yml")
    except Exception:
        log_utils.configure(level="INFO")
        logger.exception("Failed to load enricher config")
        return 1

    log_utils.configure(level="INFO", environment=config.common.environment)
    logger.info("starting enricher")

    llm_provider = config.enricher.llm_provider if config.enricher else "facade"
    llm_config_file = (
        "bedrock-config.yml" if llm_provider == "bedrock" else "facade-config.yml"
    )
    try:
        config = merge_config(config, llm_config_file)
    except Exception:
        logger.exception("Failed to load %s config", llm_provider)
        return 1

    try:
        config = merge_config(config, "metrics.yml")
    except Exception:
        logger.exception("Failed to load metrics config")
        return 1

    try:
        config = merge_config(config, "llm-tracing-config.yml")
    except Exception:
        logger.warning("Failed to load tracing config, proceeding without LLM tracing")

    return await run_enricher(config)
