"""Scribe service — sole database writer.

Each deployment is configured with exactly one SQS queue via its own config
file.  The code is fully abstract: priority (high / medium / low) is expressed
entirely through the config mounted at deployment time.

Error paths:
- NonRetryableError (malformed/invalid messages) → DLQ, message deleted
- Any other Exception (DB errors, transient failures) → SQS visibility timeout
  with exponential backoff (10s / 30s / 60s ± 20% jitter)
"""

import asyncio
import json
import random
import signal
from typing import Any

import boto3
from sqlalchemy import text

from common.config import load_config, merge_config
from common.metrics import DBMetrics, JobMetrics, ScribeMetrics, SQSMetrics
from common.metrics.telescope import TelescopeClient
from common.utils import log_utils
from common.utils.db_utils import create_long_lived_engine

from .batcher import ScribeBatcher
from .errors import MalformedMessageError, NonRetryableError

logger = log_utils.get_logger(__name__)

# Exponential backoff delays in seconds; capped at the last value
BACKOFF_DELAYS = [10, 30, 60]


def compute_backoff_with_jitter(receive_count: int) -> int:
    """Compute SQS visibility timeout with exponential backoff and ±20% jitter.

    Args:
        receive_count: Number of times the message has been received (1-based).

    Returns:
        Visibility timeout in seconds.
    """
    index = min(receive_count - 1, len(BACKOFF_DELAYS) - 1)
    base_delay = BACKOFF_DELAYS[index]
    jitter = random.uniform(-0.2, 0.2)
    return max(1, int(base_delay * (1 + jitter)))


def _extract_field(body: str, field: str) -> str:
    """Best-effort extraction of a top-level string field from a JSON body.

    Used only for metric labels; failures silently return 'unknown'.
    """
    try:
        return json.loads(body).get(field, "unknown") or "unknown"
    except Exception:
        return "unknown"


def _create_sqs_client(region: str, max_pool_connections: int = 50) -> Any:
    """Create a synchronous SQS client with a sized connection pool.

    The default urllib3 pool of 10 is too small for Scribe's concurrent
    ack/visibility-change calls and causes "Connection pool is full,
    discarding connection" warnings that stall SQS operations.
    """
    from botocore.config import Config

    session = boto3.Session(region_name=region)
    return session.client(
        "sqs",
        config=Config(max_pool_connections=max_pool_connections),
    )


async def _poll_sqs(
    sqs_client: Any,
    batcher: Any,
    queue_url: str,
    poll_cfg: dict[str, Any],
    sqs_metrics: SQSMetrics | None = None,
) -> bool:
    """Single SQS receive call; hands each message to the batcher.

    Returns True if any messages were received, False if the queue was empty.
    Raises on SQS connectivity errors so the caller can apply retry logic.

    Args:
        sqs_client: Synchronous boto3 SQS client.
        batcher: ScribeBatcher that buffers and flushes messages.
        queue_url: SQS queue URL to poll.
        poll_cfg: Dict with sqs_max_messages, sqs_wait_time_seconds,
                  sqs_visibility_timeout.
    """
    response = await asyncio.to_thread(
        sqs_client.receive_message,
        QueueUrl=queue_url,
        MaxNumberOfMessages=poll_cfg["sqs_max_messages"],
        WaitTimeSeconds=poll_cfg["sqs_wait_time_seconds"],
        VisibilityTimeout=poll_cfg["sqs_visibility_timeout"],
        AttributeNames=["ApproximateReceiveCount"],
        # RedriveRunId is stamped by scripts/dlq_inspector on messages it moves
        # back from a DLQ, so the batcher can tag its own metrics/logs with it.
        MessageAttributeNames=["RedriveRunId"],
    )

    messages = response.get("Messages", [])

    if sqs_metrics:
        queue_name = queue_url.rstrip("/").rsplit("/", 1)[-1]
        sqs_metrics.record_poll(queue_name)

    if not messages:
        return False

    logger.info(
        "scribe received messages",
        queue_url=queue_url,
        count=len(messages),
    )

    for message in messages:
        await batcher.add(message)

    return True


async def _run_single_queue_loop(
    sqs_client: Any,
    batcher: Any,
    queue_url: str,
    poll_cfg: dict[str, Any],
    shutdown_event: asyncio.Event,
    sqs_metrics: SQSMetrics | None = None,
) -> None:
    """SQS polling loop for a single dedicated queue.

    Polls continuously; sleeps sqs_poll_error_delay on an empty response or
    on a poll error before retrying. Exits when shutdown_event is set.

    Args:
        sqs_client: Synchronous boto3 SQS client.
        batcher: ScribeBatcher that buffers and flushes messages.
        queue_url: SQS queue URL to poll.
        poll_cfg: Shared SQS polling settings.
        shutdown_event: Set by the SIGTERM handler to request graceful shutdown.
    """
    while not shutdown_event.is_set():
        if batcher.is_overloaded():
            await asyncio.sleep(0.05)
            continue

        try:
            had_messages = await _poll_sqs(
                sqs_client=sqs_client,
                batcher=batcher,
                queue_url=queue_url,
                poll_cfg=poll_cfg,
                sqs_metrics=sqs_metrics,
            )
            if not had_messages:
                await asyncio.sleep(poll_cfg["sqs_poll_error_delay"])
        except Exception:
            logger.warning(
                "SQS poll error, retrying", queue_url=queue_url, exc_info=True
            )
            await asyncio.sleep(poll_cfg["sqs_poll_error_delay"])


async def main() -> int:
    """Run the Scribe SQS polling worker.

    Returns:
        0 on clean exit, 1 on configuration error.
    """
    try:
        config = load_config("matik-scribe-config.yml")
    except Exception:
        log_utils.configure(level="INFO")
        logger.exception("Failed to load scribe config")
        return 1

    log_utils.configure(level="INFO", environment=config.common.environment)
    logger.info("starting scribe")

    try:
        config = merge_config(config, "metrics.yml")
    except FileNotFoundError:
        logger.warning("metrics.yml not found, proceeding without metrics config")

    if not config.scribe:
        logger.error("scribe config section missing from matik-scribe-config.yml")
        return 1

    if not config.mysql:
        logger.error("mysql config section missing")
        return 1

    scribe_cfg = config.scribe

    # --- Telescope / metrics ---
    telescope: TelescopeClient | None = None
    db_metrics: DBMetrics | None = None
    scribe_metrics: ScribeMetrics | None = None
    sqs_metrics: SQSMetrics | None = None

    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = "scribe"
        config.telescope.environment = config.common.environment
        telescope = TelescopeClient(config.telescope)
        telescope.start()
        meter = telescope.meter
        JobMetrics(meter, "scribe")
        db_metrics = DBMetrics(meter, "scribe")
        scribe_metrics = ScribeMetrics(meter)
        sqs_metrics = SQSMetrics(meter, "scribe")

    # --- Database engine ---
    engine = create_long_lived_engine(config.mysql)

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("database connectivity check passed")
    except Exception as e:
        logger.error("database connectivity check failed", error=str(e))
        return 1

    sqs_client = _create_sqs_client(scribe_cfg.region)
    semaphore = asyncio.Semaphore(scribe_cfg.max_concurrent_writes)

    # --- DAO + handler wiring ---
    from common.daos.ghe_pr_tracker_dao import GHEPRTrackerDAO
    from common.daos.reliability_correlation_dao import ReliabilityCorrelationDAO
    from common.daos.reliability_correlation_group_dao import (
        ReliabilityCorrelationGroupDAO,
    )
    from common.datasources.registry import get_source

    from .handlers._base import HandlerHooks
    from .handlers.correlation import CorrelationHandler
    from .handlers.correlation_group import CorrelationGroupHandler
    from .handlers.generic import GenericHandler
    from .handlers.ghe_pr_tracker import GHEPRTrackerHandler
    from .hooks.enigmatologist import EnigmatologistCorrelationHook
    from .hooks.runner import HookRunner
    from .processor import ScribeProcessor

    ghe_pr_tracker_dao = GHEPRTrackerDAO(engine, db_metrics)
    correlation_dao = ReliabilityCorrelationDAO(engine, db_metrics)
    correlation_group_dao = ReliabilityCorrelationGroupDAO(engine, db_metrics)

    def _build_generic(source_type: str, hooks: HandlerHooks | None = None):  # type: ignore[no-untyped-def]
        """Build a registry-driven GenericHandler for a specced source.

        The spec's ``dao_factory`` constructs the DAO from (engine, metrics) and
        the GenericHandler reads the same spec for routing — adding a source is a
        spec registration, not a new handler class.
        """
        spec = get_source(source_type)
        assert spec.dao_factory is not None  # set on every specced source
        dao = spec.dao_factory(engine, db_metrics)
        return GenericHandler(spec, dao, hooks=hooks)

    # Incident.io is registry-driven and additionally wires the enigmatologist
    # correlation hook on base + enrichment when configured. incident_channel_summary
    # shares the same hook instance on enrichment only (it has no base-write route) —
    # both sources resolve to the same IncidentIOIncident record via record_finder.
    incidentio_hooks = HandlerHooks()
    incident_channel_summary_hooks = HandlerHooks()
    if config.enigmatologist and config.enigmatologist.sqs_queue_url:
        enig_hook = EnigmatologistCorrelationHook(
            sqs_client=sqs_client,
            queue_url=config.enigmatologist.sqs_queue_url,
        )
        incidentio_hooks = HandlerHooks(
            base=HookRunner([enig_hook], scribe_metrics),
            enrichment=HookRunner([enig_hook], scribe_metrics),
        )
        incident_channel_summary_hooks = HandlerHooks(
            enrichment=HookRunner([enig_hook], scribe_metrics),
        )
        logger.info(
            "enigmatologist correlation hook registered for base and enrichment",
            queue_url=config.enigmatologist.sqs_queue_url,
        )

    processor = ScribeProcessor(
        handlers={
            # Registry-driven GenericHandlers (spec builds DAO + routing).
            "incidentio": _build_generic("incidentio", incidentio_hooks),
            "jira": _build_generic("jira"),
            "ghe_pr": _build_generic("ghe_pr"),
            "incident_channel_summary": _build_generic(
                "incident_channel_summary", incident_channel_summary_hooks
            ),
            # Hand-wired: the tracker + correlation sources are not catalog data
            # sources (no LLM enrichment / composite crawl-progress keys) and stay
            # on their bespoke handlers this phase.
            "ghe_pr_tracker": GHEPRTrackerHandler(ghe_pr_tracker_dao),
            "correlation": CorrelationHandler(correlation_dao),
            "correlation_group": CorrelationGroupHandler(correlation_group_dao),
        }
    )

    queue = scribe_cfg.queue

    poll_cfg = {
        "sqs_max_messages": scribe_cfg.sqs_max_messages,
        "sqs_wait_time_seconds": scribe_cfg.sqs_wait_time_seconds,
        "sqs_visibility_timeout": scribe_cfg.sqs_visibility_timeout,
        "sqs_poll_error_delay": scribe_cfg.sqs_poll_error_delay,
    }

    batcher = ScribeBatcher(
        processor=processor,
        sqs_client=sqs_client,
        queue_url=queue.queue_url,
        dlq_url=queue.dlq_url,
        max_receive_count=queue.max_receive_count,
        semaphore=semaphore,
        flush_max_messages=scribe_cfg.batch_max_messages,
        flush_interval_seconds=scribe_cfg.batch_flush_interval_ms / 1000.0,
        enrichment_base_not_found_delay=scribe_cfg.enrichment_base_not_found_delay,
        scribe_metrics=scribe_metrics,
    )

    shutdown_event = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("SIGTERM received, initiating graceful shutdown")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)

    logger.info(
        "scribe starting",
        region=scribe_cfg.region,
        max_concurrent_writes=scribe_cfg.max_concurrent_writes,
        queue_url=queue.queue_url,
        dlq_url=queue.dlq_url,
        batch_max_messages=scribe_cfg.batch_max_messages,
        batch_flush_interval_ms=scribe_cfg.batch_flush_interval_ms,
    )

    await batcher.start()

    try:
        await _run_single_queue_loop(
            sqs_client=sqs_client,
            batcher=batcher,
            queue_url=queue.queue_url,
            poll_cfg=poll_cfg,
            shutdown_event=shutdown_event,
            sqs_metrics=sqs_metrics,
        )
    finally:
        await batcher.stop(drain_timeout=30)
        if telescope:
            telescope.shutdown()

    return 0


# Re-export error classes for backward compatibility
__all__ = ["MalformedMessageError", "NonRetryableError", "main"]
