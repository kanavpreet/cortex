"""Tests for the generic BaseCrawler producer/consumer dispatch machinery.

Exercises the source-agnostic loop through a minimal ``FakeCrawler`` so the
generic behavior (tracker guard, batch flush on size/timeout/sentinel, producer
error propagation, consumer-first cancellation, tracker updates, mark-sync) is
covered independently of any real source. A concrete source crawler suite
is the end-to-end oracle; these pin the base contract each source relies on.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from historian.base.crawler import (
    DONE_SENTINEL,
    BaseCrawler,
    CrawlerError,
    CrawlerResult,
)


@dataclass
class _FakeTracker:
    """Minimal tracker double."""

    status: str = "OK"
    initial_sync_complete: bool = False


@dataclass
class _FakeCrawler(BaseCrawler[int, _FakeTracker]):
    """A minimal BaseCrawler subclass driving the generic loop with ints.

    Each knob controls one hook so tests can steer the generic machinery without
    any real client, tracker API, or SQS publisher.
    """

    tracker: _FakeTracker | None = field(default_factory=_FakeTracker)
    items: list[int] = field(default_factory=list)
    batch_size: int = 2
    timeout: float = 5.0
    producer_error: Exception | None = None
    publish_returns_none: bool = False
    producer_hangs: bool = False

    # Recording for assertions.
    published_batches: list[list[int]] = field(default_factory=list)
    enrichment_batches: list[list[int]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)

    # --- hooks ---------------------------------------------------------

    def _get_tracker(self) -> _FakeTracker | None:
        return self.tracker

    def _check_tracker_status(self, tracker: _FakeTracker) -> None:
        if tracker.status == "ERROR":
            raise CrawlerError("tracker in ERROR state")

    def _update_tracker(
        self,
        tracker: _FakeTracker,
        cursor: Any | None = None,
        error: Exception | None = None,
        mark_sync_complete: bool = False,
    ) -> bool:
        self.update_calls.append(
            {"error": error, "mark_sync_complete": mark_sync_complete}
        )
        return True

    def _initial_sync_complete(self, tracker: _FakeTracker) -> bool:
        return tracker.initial_sync_complete

    def _calculate_fetch_cursor(self, tracker: _FakeTracker) -> Any:
        return None

    async def _producer(self, queue: asyncio.Queue[Any], fetch_cursor: Any) -> int:
        try:
            if self.producer_error is not None:
                raise self.producer_error
            for item in self.items:
                await queue.put(item)
            if self.producer_hangs:
                # Never send the done sentinel — simulate a stuck producer so the
                # consumer must time out.
                await asyncio.sleep(3600)
            return len(self.items)
        finally:
            if not self.producer_hangs:
                await queue.put(DONE_SENTINEL)

    async def _publish_records(self, items: list[int]) -> int | None:
        self.published_batches.append(list(items))
        return None if self.publish_returns_none else len(items)

    async def _publish_enrichment_messages(self, items: list[int]) -> None:
        self.enrichment_batches.append(list(items))

    def _write_batch_size(self) -> int:
        return self.batch_size

    def _consumer_timeout_seconds(self) -> float:
        return self.timeout


def test_dispatch_no_tracker_returns_failure() -> None:
    """A missing tracker fails the run without dispatching."""
    crawler = _FakeCrawler(tracker=None)
    result = crawler.dispatch()
    assert result == CrawlerResult(
        records_processed=0,
        success=False,
        error_message="Tracker not found in database",
    )


def test_dispatch_tracker_error_returns_failure() -> None:
    """A tracker in ERROR state aborts before dispatching."""
    crawler = _FakeCrawler(tracker=_FakeTracker(status="ERROR"))
    result = crawler.dispatch()
    assert result.success is False
    assert "ERROR state" in (result.error_message or "")


def test_dispatch_happy_path_batches_and_marks_sync() -> None:
    """Items are batched, enriched+published, and initial sync is marked."""
    crawler = _FakeCrawler(items=[1, 2, 3], batch_size=2)
    result = crawler.dispatch()

    assert result.success is True
    assert result.records_processed == 3
    # 3 items, batch_size 2 → a full batch [1, 2] then a flush of [3].
    assert crawler.published_batches == [[1, 2], [3]]
    assert crawler.enrichment_batches == [[1, 2], [3]]
    # A successful first run marks the initial sync complete.
    assert crawler.update_calls[-1]["mark_sync_complete"] is True
    assert crawler.update_calls[-1]["error"] is None


def test_dispatch_already_synced_does_not_remark() -> None:
    """A run after initial sync does not re-mark sync complete."""
    crawler = _FakeCrawler(tracker=_FakeTracker(initial_sync_complete=True), items=[1])
    result = crawler.dispatch()
    assert result.success is True
    assert crawler.update_calls[-1]["mark_sync_complete"] is False


def test_dispatch_no_items_succeeds() -> None:
    """An empty producer still succeeds (nothing to write)."""
    crawler = _FakeCrawler(items=[])
    result = crawler.dispatch()
    assert result.success is True
    assert result.records_processed == 0
    assert crawler.published_batches == []


def test_dispatch_publish_failure_returns_failure_and_records_error() -> None:
    """A batch whose write returns None fails the run and records tracker error."""
    crawler = _FakeCrawler(items=[1, 2], publish_returns_none=True)
    result = crawler.dispatch()

    assert result.success is False
    assert result.error_message == "Failed to write some records to database"
    assert crawler.update_calls[-1]["error"] is not None


def test_dispatch_producer_exception_propagates_as_failure() -> None:
    """A producer exception is surfaced as a failed CrawlerResult + tracker error."""
    crawler = _FakeCrawler(items=[1], producer_error=RuntimeError("boom"))
    result = crawler.dispatch()

    assert result.success is False
    assert "Error in async dispatch" in (result.error_message or "")
    assert "boom" in (result.error_message or "")
    assert crawler.update_calls[-1]["error"] is not None


def test_dispatch_consumer_timeout_with_stuck_producer_fails() -> None:
    """If the producer hangs and no batch buffers, the consumer times out → fail."""
    crawler = _FakeCrawler(items=[], timeout=0.05, producer_hangs=True)
    result = crawler.dispatch()
    assert result.success is False


def test_dispatch_consumer_timeout_flushes_partial_batch() -> None:
    """A partial batch buffered when the producer hangs is flushed on timeout."""
    # 1 item (< batch_size) then the producer hangs → the consumer times out and
    # flushes the buffered partial batch, succeeding.
    crawler = _FakeCrawler(items=[7], batch_size=5, timeout=0.05, producer_hangs=True)
    result = crawler.dispatch()

    assert result.success is True
    assert result.records_processed == 1
    assert crawler.published_batches == [[7]]


def test_write_failure_message_override() -> None:
    """A subclass can override the batch-write-failure wording."""

    class _CustomMsgCrawler(_FakeCrawler):
        _WRITE_FAILURE_MESSAGE = "custom failure wording"

    crawler = _CustomMsgCrawler(items=[1], publish_returns_none=True)
    result = crawler.dispatch()
    assert result.error_message == "custom failure wording"
