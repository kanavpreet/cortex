"""In-process batching layer for Scribe SQS messages.

Buffers incoming messages, groups them by (source_type, message_type), and
flushes to the database in batches when a size or time trigger fires.

Design:
- Single asyncio event loop — no locks needed; triggers serialize naturally.
- Size trigger: when buffer reaches flush_max_messages.
- Time trigger: armed when the first message lands in an empty buffer;
  fires after flush_interval_seconds; re-armed after each size-triggered flush.
- Snapshot pattern: _flush_now grabs a reference to the current buffer,
  replaces it with an empty list, then dispatches group tasks. New messages
  during in-flight flushes land in the fresh buffer.
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.utils import log_utils

from .errors import BaseRecordNotFoundError, MalformedMessageError

# Exponential backoff delays in seconds; capped at the last value (mirrors main.py)
_BACKOFF_DELAYS = [10, 30, 60]


def _compute_backoff_with_jitter(receive_count: int) -> int:
    """Compute SQS visibility timeout with exponential backoff and ±20% jitter."""
    import random

    index = min(receive_count - 1, len(_BACKOFF_DELAYS) - 1)
    base_delay = _BACKOFF_DELAYS[index]
    jitter = random.uniform(-0.2, 0.2)
    return max(1, int(base_delay * (1 + jitter)))


if TYPE_CHECKING:
    from common.metrics.scribe_metrics import ScribeMetrics

    from .processor import GroupKey, ScribeProcessor

logger = log_utils.get_logger(__name__)


@dataclass
class BufferedMessage:
    """A single SQS message held in the in-process buffer."""

    parsed: dict[str, Any]
    receipt_handle: str
    receive_count: int
    body: str
    # Set when this message carries a RedriveRunId attribute, i.e. it was moved
    # back onto the queue by scripts/dlq_inspector rather than freshly produced.
    redrive_run_id: str | None = None


class ScribeBatcher:
    """Buffers Scribe SQS messages and flushes them in groups.

    Call start() before adding messages. Call stop() for graceful shutdown.
    """

    def __init__(
        self,
        processor: "ScribeProcessor",
        sqs_client: Any,
        queue_url: str,
        dlq_url: str,
        max_receive_count: int,
        semaphore: asyncio.Semaphore,
        flush_max_messages: int,
        flush_interval_seconds: float,
        enrichment_base_not_found_delay: int,
        scribe_metrics: "ScribeMetrics | None" = None,
    ) -> None:
        self._processor = processor
        self._sqs = sqs_client
        self._queue_url = queue_url
        self._dlq_url = dlq_url
        self._max_receive_count = max_receive_count
        self._semaphore = semaphore
        self._semaphore_capacity = semaphore._value
        self._flush_max = flush_max_messages
        self._flush_interval = flush_interval_seconds
        self._not_found_delay = enrichment_base_not_found_delay
        self._metrics = scribe_metrics

        self._buffer: list[BufferedMessage] = []
        self._inflight: set[asyncio.Task[None]] = set()
        self._timer_handle: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Capture the running event loop. Must be called before add()."""
        self._loop = asyncio.get_running_loop()

    async def stop(self, drain_timeout: float = 30.0) -> None:
        """Flush remaining buffered messages and await all in-flight tasks.

        Args:
            drain_timeout: Maximum seconds to wait for in-flight flushes to
                complete before giving up and logging a warning.
        """
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None

        if self._buffer:
            logger.info(
                "draining batcher",
                buffered=len(self._buffer),
                inflight=len(self._inflight),
            )
            self._flush_now("shutdown")

        if self._inflight:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(self._inflight), return_exceptions=True),
                    timeout=drain_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "batcher drain timed out",
                    inflight=len(self._inflight),
                    timeout=drain_timeout,
                )

        logger.info("batcher stopped", remaining_inflight=len(self._inflight))

    def is_overloaded(self) -> bool:
        """Return True when in-flight tasks exceed twice the semaphore capacity.

        The polling loop should pause on True to prevent unbounded task growth
        under sustained DB slowness.
        """
        return len(self._inflight) >= self._semaphore_capacity * 2

    async def add(self, raw: dict[str, Any]) -> None:
        """Accept a raw SQS message dict and append to the buffer.

        Validates the message before buffering. Invalid messages are sent
        directly to the DLQ and deleted from the queue.

        Args:
            raw: Raw SQS message dict with keys Body, ReceiptHandle, Attributes.
        """
        body = raw.get("Body", "")
        receipt_handle = raw["ReceiptHandle"]
        receive_count = int(
            raw.get("Attributes", {}).get("ApproximateReceiveCount", "1")
        )
        redrive_run_id = (
            raw.get("MessageAttributes", {}).get("RedriveRunId", {}).get("StringValue")
        )

        try:
            validated = self._processor.parse_and_validate(body)
        except MalformedMessageError as e:
            await self._send_to_dlq(body, receipt_handle, str(e), redrive_run_id)
            return

        bm = BufferedMessage(
            parsed=validated.parsed,
            receipt_handle=receipt_handle,
            receive_count=receive_count,
            body=body,
            redrive_run_id=redrive_run_id,
        )
        self._buffer.append(bm)

        if len(self._buffer) == 1 and self._timer_handle is None:
            assert self._loop is not None
            self._timer_handle = self._loop.call_later(
                self._flush_interval, self._flush_due
            )

        if len(self._buffer) >= self._flush_max:
            self._flush_now("size")

    def _flush_due(self) -> None:
        """Timer callback — fires on the event loop thread."""
        self._timer_handle = None
        if self._buffer:
            self._flush_now("timer")

    def _flush_now(self, trigger: str) -> None:
        """Snapshot the buffer, reset it, and spawn tasks per group or per message.

        Base messages are grouped by (source_type, message_type) and flushed as
        a batch — one DB connection per group. Enrichment messages are dispatched
        one task per message so a stuck record (base not yet written) cannot block
        or poison its siblings.

        Args:
            trigger: Label for logging ("size", "timer", "shutdown").
        """
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None

        snapshot = self._buffer
        self._buffer = []

        if not snapshot:
            return

        from .processor import GroupKey

        base_groups: dict[GroupKey, list[BufferedMessage]] = defaultdict(list)
        enrichment_singles: list[tuple[GroupKey, BufferedMessage]] = []

        for bm in snapshot:
            key = GroupKey(
                source_type=bm.parsed["source_type"],
                message_type=bm.parsed["message_type"],
            )
            if bm.parsed["message_type"] == "enrichment":
                enrichment_singles.append((key, bm))
            else:
                base_groups[key].append(bm)

        task_count = len(base_groups) + len(enrichment_singles)
        logger.info(
            "scribe flushing",
            trigger=trigger,
            total=len(snapshot),
            tasks=task_count,
        )

        for key, msgs in base_groups.items():
            task = asyncio.create_task(self._flush_group(key, msgs))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

        for key, bm in enrichment_singles:
            task = asyncio.create_task(self._flush_group(key, [bm]))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _flush_group(self, key: "GroupKey", msgs: list[BufferedMessage]) -> None:
        """Flush one (source_type, message_type) group under the semaphore."""

        start = time.perf_counter()
        # A group's outcome (success/failure) applies uniformly to every message
        # in it, so knowing whether *any* member was redriven is enough to know
        # whether the redrive succeeded — no per-message tracking needed.
        redrive_run_ids = sorted({m.redrive_run_id for m in msgs if m.redrive_run_id})
        redriven = bool(redrive_run_ids)

        async with self._semaphore:
            try:
                parsed_list = [m.parsed for m in msgs]
                await self._processor.dispatch_batch(key, parsed_list)

                duration = time.perf_counter() - start
                if self._metrics:
                    self._metrics.record_batch_flush(
                        key.source_type,
                        key.message_type,
                        len(msgs),
                        duration,
                        "success",
                        redriven=redriven,
                    )
                if redrive_run_ids:
                    logger.info(
                        "redriven message group flushed successfully",
                        source_type=key.source_type,
                        message_type=key.message_type,
                        size=len(msgs),
                        redrive_run_ids=redrive_run_ids,
                    )

                await asyncio.gather(
                    *[self._ack_message(m.receipt_handle) for m in msgs]
                )

            except MalformedMessageError as e:
                logger.error(
                    "group-level MalformedMessageError (should not happen after admission)",
                    source_type=key.source_type,
                    message_type=key.message_type,
                    size=len(msgs),
                    error=str(e),
                    redrive_run_ids=redrive_run_ids or None,
                )
                if self._metrics:
                    self._metrics.record_group_failure(
                        key.source_type, key.message_type, len(msgs), redriven=redriven
                    )
                await asyncio.gather(
                    *[self._extend_visibility(m) for m in msgs],
                    return_exceptions=True,
                )

            except BaseRecordNotFoundError as e:
                # Enrichment arrived before its base record exists. Apply the
                # long configured delay to all messages in the group so they
                # are retried after the base write has had time to land.
                logger.info(
                    "base record not found, deferring enrichment group",
                    source_type=key.source_type,
                    message_type=key.message_type,
                    size=len(msgs),
                    error=str(e),
                    redrive_run_ids=redrive_run_ids or None,
                )
                await asyncio.gather(
                    *[self._extend_visibility_not_found(m) for m in msgs],
                    return_exceptions=True,
                )

            except Exception as e:
                # Check if this looks like a DAO returning None (group-level transient)
                # RuntimeError from our handlers uses the "DB upsert failed" pattern.
                # Any other Exception is also treated as a group-level transient.
                logger.warning(
                    "group-level transient failure, extending visibility on all",
                    source_type=key.source_type,
                    message_type=key.message_type,
                    size=len(msgs),
                    error=str(e),
                    redrive_run_ids=redrive_run_ids or None,
                )
                if self._metrics:
                    self._metrics.record_group_failure(
                        key.source_type, key.message_type, len(msgs), redriven=redriven
                    )
                await asyncio.gather(
                    *[self._extend_visibility(m) for m in msgs],
                    return_exceptions=True,
                )

    async def _ack_message(self, receipt_handle: str) -> None:
        await asyncio.to_thread(
            self._sqs.delete_message,
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )

    async def _extend_visibility(self, msg: BufferedMessage) -> None:
        if msg.receive_count >= self._max_receive_count:
            logger.error(
                "max receive count reached, SQS will redrive to DLQ",
                receive_count=msg.receive_count,
                redrive_run_id=msg.redrive_run_id,
            )
            if self._metrics:
                source = msg.parsed.get("source_type", "unknown")
                self._metrics.record_dlq_message(
                    source, "RetryableError", redriven=bool(msg.redrive_run_id)
                )
        else:
            backoff = _compute_backoff_with_jitter(msg.receive_count)
            await asyncio.to_thread(
                self._sqs.change_message_visibility,
                QueueUrl=self._queue_url,
                ReceiptHandle=msg.receipt_handle,
                VisibilityTimeout=backoff,
            )

    async def _extend_visibility_not_found(self, msg: BufferedMessage) -> None:
        await asyncio.to_thread(
            self._sqs.change_message_visibility,
            QueueUrl=self._queue_url,
            ReceiptHandle=msg.receipt_handle,
            VisibilityTimeout=self._not_found_delay,
        )

    async def _send_to_dlq(
        self,
        body: str,
        receipt_handle: str,
        error: str,
        redrive_run_id: str | None = None,
    ) -> None:
        logger.error(
            "malformed message at admission, sending to DLQ",
            error=error,
            redrive_run_id=redrive_run_id,
        )
        await asyncio.to_thread(
            self._sqs.send_message,
            QueueUrl=self._dlq_url,
            MessageBody=body,
        )
        await asyncio.to_thread(
            self._sqs.delete_message,
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
        if self._metrics:
            self._metrics.record_dlq_message(
                "unknown", "MalformedMessageError", redriven=bool(redrive_run_id)
            )
