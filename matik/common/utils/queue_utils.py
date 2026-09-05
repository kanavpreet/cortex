# ---------------------------------------------------------------------------
# Queue depth polling
# ---------------------------------------------------------------------------


import asyncio
import contextlib
from typing import Any

from common.metrics.enricher_metrics import EnricherMetrics
from common.models.matik_config import MatikConfig
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


async def poll_queue_depth(
    sqs_client: Any,
    config: MatikConfig,
    enricher_metrics: EnricherMetrics,
    shutdown_event: asyncio.Event,
    interval: int = 60,
) -> None:
    """Background task that periodically polls queue depth for both queues.

    Records ``matik_enricher_queue_depth`` for both the enricher input queue
    (label: "enricher") and the DLQ (label: "dlq").

    Args:
        sqs_client: Async SQSClient instance.
        config: MatikConfig with config.enricher populated.
        enricher_metrics: EnricherMetrics instance to record gauge values.
        shutdown_event: Set this event to stop polling.
        interval: Seconds between polls. Defaults to 60.
    """
    assert config.enricher is not None
    enricher_cfg = config.enricher

    while not shutdown_event.is_set():
        for queue_label, queue_url in [
            ("enricher", enricher_cfg.enricher_queue_url),
            ("dlq", enricher_cfg.enricher_dlq_url),
        ]:
            try:
                attrs = await sqs_client.get_queue_attributes(
                    queue_url,
                    ["ApproximateNumberOfMessages"],
                )
                depth = int(attrs.get("ApproximateNumberOfMessages", "0"))
                enricher_metrics.record_queue_depth(queue_label, depth)
                logger.debug("Queue depth recorded", queue=queue_label, depth=depth)
            except Exception:
                logger.warning(
                    "Failed to poll queue depth", queue=queue_label, exc_info=True
                )

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
