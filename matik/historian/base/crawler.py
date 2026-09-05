"""Generic producer/consumer batch-crawl skeleton shared by historian crawlers.

``BaseCrawler`` hosts the source-agnostic dispatch machinery that was previously
hand-written in each source crawler: a bounded ``asyncio.Queue`` between a
producer (page fetch) and a consumer (batched write), with the producer/consumer
orchestrated so neither deadlocks the other, a consumer-side batch flush on
size/timeout/done-sentinel, and tracker status/update bookkeeping around the run.

Subclasses implement only the irreducible per-source hooks — how to read/check/
update the tracker, how to compute the fetch cursor, how to fetch pages (the
producer body), how to publish base + enrichment messages, and the batch-size /
consumer-timeout config accessors. The generic parts (``dispatch``,
``_dispatch_async``, ``_consumer``, ``_process_batch``, the queue orchestration
and the ``None`` done-sentinel protocol) are provided here and are not overridden.

``CrawlerError`` and ``CrawlerResult`` live here and are re-exported by the
concrete crawler modules for backward-compatible imports.
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Bounded queue size buffering items between the producer and consumer. Keeps
# memory flat while letting the producer run ahead of the writer.
QUEUE_MAXSIZE = 200

# Sentinel item the producer puts on the queue to signal it is finished.
DONE_SENTINEL = None


class CrawlerError(Exception):
    """Exception raised when a crawler encounters an unrecoverable error."""

    pass


@dataclass
class CrawlerResult:
    """Result of a crawler run.

    ``records_processed`` is the count of items written across all batches,
    regardless of source (incidents, PRs, issues, ...).
    """

    records_processed: int
    success: bool
    error_message: str | None = None


class BaseCrawler[TItem, TTracker](ABC):
    """Base crawler with a generic producer/consumer batch-dispatch loop.

    Type parameters:
        TItem: the item type the producer fetches and the consumer writes.
        TTracker: the source's incremental-sync tracker type.

    Subclasses implement the abstract hooks below and inherit ``dispatch`` /
    ``_dispatch_async`` / ``_consumer`` / ``_process_batch`` unchanged.
    """

    # Error message set on the tracker + CrawlerResult when a batch write fails.
    # Overridable so a source can keep its historical wording.
    _WRITE_FAILURE_MESSAGE: str = "Failed to write some records to database"

    # ------------------------------------------------------------------ hooks

    @abstractmethod
    def _get_tracker(self) -> TTracker | None:
        """Load the current sync tracker (e.g. via the Matik API).

        Returns ``None`` if no tracker exists — the run aborts as a failure.
        """

    @abstractmethod
    def _check_tracker_status(self, tracker: TTracker) -> None:
        """Raise ``CrawlerError`` if the tracker is in a non-runnable state."""

    @abstractmethod
    def _update_tracker(
        self,
        tracker: TTracker,
        cursor: Any | None = None,
        error: Exception | None = None,
        mark_sync_complete: bool = False,
    ) -> bool:
        """Persist tracker progress (or an error) after a run attempt."""

    @abstractmethod
    def _initial_sync_complete(self, tracker: TTracker) -> bool:
        """Whether the tracker's initial (backfill) sync has already completed.

        Drives whether a successful run marks the initial sync complete.
        """

    @abstractmethod
    def _calculate_fetch_cursor(self, tracker: TTracker) -> Any:
        """Compute the fetch cursor (date filter / pagination floor) for this run."""

    @abstractmethod
    async def _producer(self, queue: asyncio.Queue[Any], fetch_cursor: Any) -> int:
        """Fetch pages and ``queue.put(item)`` each; return the count fetched.

        Implementations MUST ``queue.put(DONE_SENTINEL)`` in a ``finally`` so the
        consumer always terminates, even on error.
        """

    @abstractmethod
    async def _publish_records(self, items: list[TItem]) -> int | None:
        """Publish a batch of base records (e.g. to SQS for Scribe).

        Returns the number published, or ``None`` on error (fails the batch).
        """

    @abstractmethod
    async def _publish_enrichment_messages(self, items: list[TItem]) -> None:
        """Publish enrichment requests for a batch (async LLM path)."""

    @abstractmethod
    def _write_batch_size(self) -> int:
        """Number of items to accumulate before flushing a write batch."""

    @abstractmethod
    def _consumer_timeout_seconds(self) -> float:
        """Seconds the consumer waits for a queue item before flushing/failing."""

    # -------------------------------------------------------------- generic

    def dispatch(self) -> CrawlerResult:
        """Run the crawl: load tracker, guard its status, then dispatch async.

        Returns:
            CrawlerResult with the outcome of the crawl.
        """
        logger.info("Starting crawler", crawler=type(self).__name__)

        tracker = self._get_tracker()
        if tracker is None:
            return CrawlerResult(
                records_processed=0,
                success=False,
                error_message="Tracker not found in database",
            )

        try:
            self._check_tracker_status(tracker)
        except CrawlerError as e:
            logger.error("Tracker is in ERROR state", error=str(e))
            return CrawlerResult(
                records_processed=0,
                success=False,
                error_message=str(e),
            )

        return asyncio.run(self._dispatch_async(tracker))

    async def _dispatch_async(self, tracker: TTracker) -> CrawlerResult:
        """Async producer/consumer orchestration for a single crawl run.

        Args:
            tracker: Current tracker state.

        Returns:
            CrawlerResult with the outcome of the crawl.
        """
        fetch_cursor = self._calculate_fetch_cursor(tracker)

        # Queue to buffer items between producer and consumer.
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

        total_processed = 0

        try:
            producer_task = asyncio.create_task(self._producer(queue, fetch_cursor))
            consumer_task = asyncio.create_task(self._consumer(queue))

            # Wait for either task to finish first; prevents a deadlock if the
            # consumer exits early (timeout) while the producer blocks on put().
            done, _ = await asyncio.wait(
                [producer_task, consumer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if producer_task in done:
                # Producer finished first (normal case). Surface its exception.
                producer_exc = producer_task.exception()
                if producer_exc:
                    consumer_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await consumer_task
                    raise producer_exc

                # Producer done; let the consumer drain remaining items.
                consumer_success, total_processed = await consumer_task
            else:
                # Consumer finished first (likely a timeout). Cancel the producer
                # to prevent it deadlocking on queue.put().
                logger.warning(
                    "Consumer exited before producer finished, cancelling producer"
                )
                producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer_task

                consumer_success, total_processed = consumer_task.result()

            if not consumer_success:
                error = Exception(self._WRITE_FAILURE_MESSAGE)
                self._update_tracker(tracker, cursor=fetch_cursor, error=error)
                return CrawlerResult(
                    records_processed=total_processed,
                    success=False,
                    error_message=self._WRITE_FAILURE_MESSAGE,
                )

        except Exception as e:
            logger.exception("Error in async dispatch")
            self._update_tracker(tracker, cursor=fetch_cursor, error=e)
            return CrawlerResult(
                records_processed=total_processed,
                success=False,
                error_message=f"Error in async dispatch: {e}",
            )

        # Mark the initial sync complete if this was the first successful run.
        mark_complete = not self._initial_sync_complete(tracker)
        self._update_tracker(
            tracker, cursor=fetch_cursor, mark_sync_complete=mark_complete
        )

        if total_processed == 0:
            logger.info("No new records to process")
        else:
            logger.info(
                "Crawler completed successfully",
                records_processed=total_processed,
            )

        return CrawlerResult(
            records_processed=total_processed,
            success=True,
        )

    async def _consumer(self, queue: asyncio.Queue[Any]) -> tuple[bool, int]:
        """Read items from the queue and write them in batches.

        Flushes a batch when it reaches ``_write_batch_size()``, when the
        producer's ``DONE_SENTINEL`` arrives, or (if a partial batch is buffered)
        when the queue is idle for ``_consumer_timeout_seconds()``. An idle
        timeout with nothing buffered means the producer is stuck → failure.

        Returns:
            (success, total_processed) — success is False if any batch failed to
            write or the producer appears stuck.
        """
        batch_size = self._write_batch_size()
        consumer_timeout = self._consumer_timeout_seconds()

        batch: list[TItem] = []
        batch_count = 0
        total_processed = 0
        success = True

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=consumer_timeout)
            except TimeoutError:
                logger.warning(
                    "Consumer: timeout waiting for items",
                    batch_size=len(batch),
                )
                if batch:
                    batch_count += 1
                    if not await self._process_batch(batch, batch_count):
                        success = False
                    total_processed += len(batch)
                else:
                    # No buffered batch and a timeout — producer may be stuck.
                    success = False
                break

            # None signals the producer is done.
            if item is DONE_SENTINEL:
                if batch:
                    batch_count += 1
                    if not await self._process_batch(batch, batch_count):
                        success = False
                    total_processed += len(batch)
                break

            batch.append(item)

            # Flush when the batch is full.
            if len(batch) >= batch_size:
                batch_count += 1
                if not await self._process_batch(batch, batch_count):
                    success = False
                total_processed += len(batch)
                batch = []

        logger.info(
            "Consumer: finished",
            total_batches=batch_count,
            total_records=total_processed,
        )
        return success, total_processed

    async def _process_batch(self, batch: list[TItem], batch_num: int) -> bool:
        """Enrich then write a batch.

        Enrichment requests are published first (the async LLM path), then the
        base records are published for Scribe to write.

        Returns:
            True if the base-record write succeeded, False otherwise.
        """
        logger.info(
            "Consumer: publishing enrichment messages",
            batch=batch_num,
            count=len(batch),
        )
        await self._publish_enrichment_messages(batch)

        logger.info(
            "Consumer: writing batch",
            batch=batch_num,
            count=len(batch),
        )
        result = await self._publish_records(batch)
        return result is not None
