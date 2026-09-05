"""Tests for ScribeBatcher."""

import asyncio
import json
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from scribe.batcher import ScribeBatcher
from scribe.errors import BaseRecordNotFoundError, MalformedMessageError
from scribe.processor import GroupKey, ScribeProcessor, ValidatedMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sqs_message(
    source_type: str = "incidentio",
    message_type: str = "base",
    receipt_handle: str = "rh-1",
    receive_count: str = "1",
    extra: dict[str, Any] | None = None,
    redrive_run_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source_type": source_type,
        "message_type": message_type,
        "data": {},
    }
    if extra:
        body.update(extra)
    msg: dict[str, Any] = {
        "Body": json.dumps(body),
        "ReceiptHandle": receipt_handle,
        "Attributes": {"ApproximateReceiveCount": receive_count},
    }
    if redrive_run_id is not None:
        msg["MessageAttributes"] = {
            "RedriveRunId": {"DataType": "String", "StringValue": redrive_run_id}
        }
    return msg


def _make_batcher(
    processor: ScribeProcessor | None = None,
    sqs_client: Any = None,
    flush_max_messages: int = 5,
    flush_interval_seconds: float = 60.0,
    max_receive_count: int = 5,
    scribe_metrics: Any = None,
) -> ScribeBatcher:
    if processor is None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock()

    if sqs_client is None:
        sqs_client = MagicMock()
        sqs_client.delete_message = MagicMock()
        sqs_client.send_message = MagicMock()
        sqs_client.change_message_visibility = MagicMock()

    return ScribeBatcher(
        processor=processor,
        sqs_client=sqs_client,
        queue_url="https://sqs.example.com/queue",
        dlq_url="https://sqs.example.com/dlq",
        max_receive_count=max_receive_count,
        semaphore=asyncio.Semaphore(10),
        flush_max_messages=flush_max_messages,
        flush_interval_seconds=flush_interval_seconds,
        enrichment_base_not_found_delay=60,
        scribe_metrics=scribe_metrics,
    )


# Patch asyncio.to_thread in batcher so SQS calls don't need threads in tests
@pytest.fixture(autouse=True)
def no_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    monkeypatch.setattr("scribe.batcher.asyncio.to_thread", fake_to_thread)


# ---------------------------------------------------------------------------
# TestBatcherValidation
# ---------------------------------------------------------------------------


class TestBatcherValidation:
    @pytest.mark.asyncio
    async def test_malformed_message_sent_to_dlq_not_buffered(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            side_effect=MalformedMessageError("bad json")
        )
        sqs = MagicMock()
        sqs.send_message = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(processor=processor, sqs_client=sqs)
        await batcher.start()

        msg = _make_sqs_message()
        await batcher.add(msg)

        # Sent to DLQ and deleted, not buffered
        sqs.send_message.assert_called_once()
        sqs.delete_message.assert_called_once()
        assert len(batcher._buffer) == 0

    @pytest.mark.asyncio
    async def test_valid_message_is_buffered(self) -> None:
        batcher = _make_batcher()
        await batcher.start()

        await batcher.add(_make_sqs_message())
        assert len(batcher._buffer) == 1


# ---------------------------------------------------------------------------
# TestSizeTrigger
# ---------------------------------------------------------------------------


class TestSizeTrigger:
    @pytest.mark.asyncio
    async def test_flush_triggers_at_max_size(self) -> None:
        batcher = _make_batcher(flush_max_messages=3)
        await batcher.start()

        for i in range(3):
            await batcher.add(_make_sqs_message(receipt_handle=f"rh-{i}"))

        # Buffer should have been snapshotted (reset to empty)
        assert len(batcher._buffer) == 0
        # One group task was spawned
        await asyncio.sleep(0)  # let the event loop run the task
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_message_after_flush_lands_in_fresh_buffer(self) -> None:
        batcher = _make_batcher(flush_max_messages=2)
        await batcher.start()

        for i in range(2):
            await batcher.add(_make_sqs_message(receipt_handle=f"rh-{i}"))

        # Size trigger fired — buffer reset
        assert len(batcher._buffer) == 0

        # 3rd message lands in the new buffer
        await batcher.add(_make_sqs_message(receipt_handle="rh-3"))
        assert len(batcher._buffer) == 1


# ---------------------------------------------------------------------------
# TestTimeTrigger
# ---------------------------------------------------------------------------


class TestTimeTrigger:
    @pytest.mark.asyncio
    async def test_timer_armed_on_first_add(self) -> None:
        batcher = _make_batcher(flush_max_messages=100, flush_interval_seconds=60.0)
        await batcher.start()

        assert batcher._timer_handle is None
        await batcher.add(_make_sqs_message())
        assert batcher._timer_handle is not None

    @pytest.mark.asyncio
    async def test_timer_cancelled_on_size_trigger(self) -> None:
        batcher = _make_batcher(flush_max_messages=2, flush_interval_seconds=60.0)
        await batcher.start()

        await batcher.add(_make_sqs_message(receipt_handle="rh-0"))
        assert batcher._timer_handle is not None

        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        # Size trigger flushed — timer was cancelled
        assert batcher._timer_handle is None

    @pytest.mark.asyncio
    async def test_timer_fires_flush(self) -> None:
        batcher = _make_batcher(flush_max_messages=100, flush_interval_seconds=0.01)
        await batcher.start()

        await batcher.add(_make_sqs_message())
        assert len(batcher._buffer) == 1

        # Let the timer fire
        await asyncio.sleep(0.05)

        # Buffer should have been flushed
        assert len(batcher._buffer) == 0
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)


# ---------------------------------------------------------------------------
# TestGroupingByKey
# ---------------------------------------------------------------------------


class TestGroupingByKey:
    @pytest.mark.asyncio
    async def test_messages_grouped_by_source_and_type(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        dispatched: list[tuple[GroupKey, list[dict[str, Any]]]] = []

        def parse(body: str) -> ValidatedMessage:
            import json as _json

            parsed = _json.loads(body)
            return ValidatedMessage(
                parsed=parsed,
                source_type=parsed["source_type"],
                message_type=parsed["message_type"],
            )

        async def dispatch(key: GroupKey, lst: list[dict[str, Any]]) -> None:
            dispatched.append((key, lst))

        processor.parse_and_validate = MagicMock(side_effect=parse)
        processor.dispatch_batch = AsyncMock(side_effect=dispatch)

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=4
        )
        await batcher.start()

        await batcher.add(_make_sqs_message("incidentio", "base", "rh-1"))
        await batcher.add(_make_sqs_message("jira", "base", "rh-2"))
        await batcher.add(_make_sqs_message("incidentio", "base", "rh-3"))
        await batcher.add(_make_sqs_message("jira", "base", "rh-4"))  # triggers flush

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        keys = {d[0] for d in dispatched}
        assert GroupKey("incidentio", "base") in keys
        assert GroupKey("jira", "base") in keys

        inc_group = next(
            d[1] for d in dispatched if d[0] == GroupKey("incidentio", "base")
        )
        assert len(inc_group) == 2
        jira_group = next(d[1] for d in dispatched if d[0] == GroupKey("jira", "base"))
        assert len(jira_group) == 2

    @pytest.mark.asyncio
    async def test_enrichment_messages_dispatched_individually(self) -> None:
        """Each enrichment message spawns its own task, regardless of source_type."""
        processor = MagicMock(spec=ScribeProcessor)
        dispatched: list[tuple[GroupKey, list[dict[str, Any]]]] = []

        def parse(body: str) -> ValidatedMessage:
            import json as _json

            parsed = _json.loads(body)
            return ValidatedMessage(
                parsed=parsed,
                source_type=parsed["source_type"],
                message_type=parsed["message_type"],
            )

        async def dispatch(key: GroupKey, lst: list[dict[str, Any]]) -> None:
            dispatched.append((key, lst))

        processor.parse_and_validate = MagicMock(side_effect=parse)
        processor.dispatch_batch = AsyncMock(side_effect=dispatch)

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=3
        )
        await batcher.start()

        await batcher.add(_make_sqs_message("incidentio", "enrichment", "rh-1"))
        await batcher.add(_make_sqs_message("incidentio", "enrichment", "rh-2"))
        await batcher.add(
            _make_sqs_message("incidentio", "enrichment", "rh-3")
        )  # triggers flush

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        # Three separate dispatch calls, each with a single message
        assert len(dispatched) == 3
        for _, batch in dispatched:
            assert len(batch) == 1

    @pytest.mark.asyncio
    async def test_enrichment_failure_does_not_affect_sibling(self) -> None:
        """A BaseRecordNotFoundError on one enrichment message leaves the other unaffected."""
        processor = MagicMock(spec=ScribeProcessor)
        call_count = 0

        def parse(body: str) -> ValidatedMessage:
            import json as _json

            parsed = _json.loads(body)
            return ValidatedMessage(
                parsed=parsed,
                source_type=parsed["source_type"],
                message_type=parsed["message_type"],
            )

        async def dispatch(key: GroupKey, lst: list[dict[str, Any]]) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BaseRecordNotFoundError("base not found")

        processor.parse_and_validate = MagicMock(side_effect=parse)
        processor.dispatch_batch = AsyncMock(side_effect=dispatch)

        sqs = MagicMock()
        sqs.delete_message = MagicMock()
        sqs.change_message_visibility = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=2
        )
        await batcher.start()

        await batcher.add(_make_sqs_message("incidentio", "enrichment", "rh-1"))
        await batcher.add(_make_sqs_message("incidentio", "enrichment", "rh-2"))

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        # First message deferred, second message acked
        sqs.change_message_visibility.assert_called_once()
        assert sqs.change_message_visibility.call_args.kwargs["ReceiptHandle"] == "rh-1"
        sqs.delete_message.assert_called_once()
        assert sqs.delete_message.call_args.kwargs["ReceiptHandle"] == "rh-2"


# ---------------------------------------------------------------------------
# TestGroupSuccess
# ---------------------------------------------------------------------------


class TestGroupSuccess:
    @pytest.mark.asyncio
    async def test_all_messages_acked_on_success(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock()

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=2
        )
        await batcher.start()

        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        await batcher.add(_make_sqs_message(receipt_handle="rh-2"))

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        assert sqs.delete_message.call_count == 2


# ---------------------------------------------------------------------------
# TestGroupReturnsNone (group-level transient failure)
# ---------------------------------------------------------------------------


class TestGroupTransientFailure:
    @pytest.mark.asyncio
    async def test_visibility_extended_on_group_failure(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock(side_effect=RuntimeError("DB error"))

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=2
        )
        await batcher.start()

        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        await batcher.add(_make_sqs_message(receipt_handle="rh-2"))

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        # Visibility extended, not deleted
        assert sqs.change_message_visibility.call_count == 2
        sqs.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_metrics_group_failure_recorded(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock(side_effect=RuntimeError("oops"))

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()

        metrics = MagicMock()
        metrics.record_group_failure = MagicMock()

        batcher = _make_batcher(
            processor=processor,
            sqs_client=sqs,
            flush_max_messages=1,
            scribe_metrics=metrics,
        )
        await batcher.start()

        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        metrics.record_group_failure.assert_called_once()


# ---------------------------------------------------------------------------
# TestBaseRecordNotFound
# ---------------------------------------------------------------------------


class TestBaseRecordNotFound:
    @pytest.mark.asyncio
    async def test_not_found_delay_applied_not_backoff(self) -> None:
        """BaseRecordNotFoundError uses enrichment_base_not_found_delay, not exponential backoff."""
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "enrichment",
                    "data": {},
                },
                source_type="incidentio",
                message_type="enrichment",
            )
        )
        processor.dispatch_batch = AsyncMock(
            side_effect=BaseRecordNotFoundError("base not found")
        )

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()
        sqs.delete_message = MagicMock()

        not_found_delay = 120
        batcher = ScribeBatcher(
            processor=processor,
            sqs_client=sqs,
            queue_url="https://sqs.example.com/queue",
            dlq_url="https://sqs.example.com/dlq",
            max_receive_count=5,
            semaphore=asyncio.Semaphore(10),
            flush_max_messages=2,
            flush_interval_seconds=60.0,
            enrichment_base_not_found_delay=not_found_delay,
        )
        await batcher.start()

        await batcher.add(
            _make_sqs_message(message_type="enrichment", receipt_handle="rh-1")
        )
        await batcher.add(
            _make_sqs_message(message_type="enrichment", receipt_handle="rh-2")
        )

        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        # Both messages deferred with the not-found delay, not the backoff values
        assert sqs.change_message_visibility.call_count == 2
        sqs.delete_message.assert_not_called()
        for call in sqs.change_message_visibility.call_args_list:
            assert call.kwargs["VisibilityTimeout"] == not_found_delay


# ---------------------------------------------------------------------------
# TestSnapshotIsolation
# ---------------------------------------------------------------------------


class TestSnapshotIsolation:
    @pytest.mark.asyncio
    async def test_new_add_during_flush_dispatched_separately(self) -> None:
        """Messages added while a flush is in progress are dispatched in a separate call."""
        calls: list[list[dict[str, Any]]] = []
        dispatch_started = asyncio.Event()
        dispatch_unblocked = asyncio.Event()

        processor = MagicMock(spec=ScribeProcessor)

        call_count = 0

        def parse(body: str) -> ValidatedMessage:
            import json as _j

            parsed = _j.loads(body)
            return ValidatedMessage(
                parsed=parsed,
                source_type="incidentio",
                message_type="base",
            )

        async def slow_dispatch(key: GroupKey, lst: list[dict[str, Any]]) -> None:
            nonlocal call_count
            calls.append(list(lst))
            call_count += 1
            if call_count == 1:
                dispatch_started.set()
                await dispatch_unblocked.wait()

        processor.parse_and_validate = MagicMock(side_effect=parse)
        processor.dispatch_batch = AsyncMock(side_effect=slow_dispatch)

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        # flush_max_messages=2 so the second add doesn't immediately flush
        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=2
        )
        await batcher.start()

        # Force a timer flush for the first message
        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        batcher._flush_due()  # manually fire timer
        await dispatch_started.wait()

        # Second add while first flush is in flight — lands in fresh buffer
        await batcher.add(_make_sqs_message(receipt_handle="rh-2"))
        assert len(batcher._buffer) == 1
        assert batcher._buffer[0].receipt_handle == "rh-2"

        dispatch_unblocked.set()
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)
        # First dispatch saw only the first message
        assert len(calls[0]) == 1


# ---------------------------------------------------------------------------
# TestStopDrains
# ---------------------------------------------------------------------------


class TestStopDrains:
    @pytest.mark.asyncio
    async def test_stop_flushes_buffered_messages(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock()

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, flush_max_messages=100
        )
        await batcher.start()

        # Add without triggering size flush
        await batcher.add(_make_sqs_message(receipt_handle="rh-1"))
        assert len(batcher._buffer) == 1

        await batcher.stop(drain_timeout=5.0)

        assert len(batcher._buffer) == 0
        processor.dispatch_batch.assert_called_once()
        sqs.delete_message.assert_called_once()


# ---------------------------------------------------------------------------
# TestBackpressure
# ---------------------------------------------------------------------------


class TestBackpressure:
    def test_not_overloaded_when_inflight_below_threshold(self) -> None:
        batcher = _make_batcher()
        batcher._semaphore = asyncio.Semaphore(5)
        batcher._semaphore_capacity = 5
        # 0 inflight < 5*2=10
        assert not batcher.is_overloaded()

    @pytest.mark.asyncio
    async def test_overloaded_when_inflight_at_threshold(self) -> None:
        batcher = _make_batcher()
        batcher._semaphore = asyncio.Semaphore(2)
        batcher._semaphore_capacity = 2
        # Add 4 fake tasks to inflight (2*2=4)
        tasks: list[asyncio.Task[None]] = []

        async def placeholder() -> None:
            await asyncio.sleep(100)

        for _ in range(4):
            task = asyncio.create_task(placeholder())
            batcher._inflight.add(task)
            tasks.append(task)

        try:
            assert batcher.is_overloaded()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# TestMaxReceiveCount
# ---------------------------------------------------------------------------


class TestMaxReceiveCount:
    @pytest.mark.asyncio
    async def test_no_visibility_extension_at_max_receive_count(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock(side_effect=RuntimeError("transient"))

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()

        batcher = _make_batcher(
            processor=processor,
            sqs_client=sqs,
            flush_max_messages=1,
            max_receive_count=5,
        )
        await batcher.start()

        # Message at max receive count
        msg = _make_sqs_message(receive_count="5")
        await batcher.add(msg)
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        # No visibility extension — SQS will redrive automatically
        sqs.change_message_visibility.assert_not_called()


# ---------------------------------------------------------------------------
# TestRedriveTagging
# ---------------------------------------------------------------------------


class TestRedriveTagging:
    @pytest.mark.asyncio
    async def test_redrive_run_id_extracted_into_buffer(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )

        batcher = _make_batcher(processor=processor, flush_max_messages=100)
        await batcher.start()

        await batcher.add(_make_sqs_message(redrive_run_id="run-1"))
        await batcher.add(_make_sqs_message(receipt_handle="rh-2"))

        assert batcher._buffer[0].redrive_run_id == "run-1"
        assert batcher._buffer[1].redrive_run_id is None

    @pytest.mark.asyncio
    async def test_successful_flush_of_redriven_group_tags_metrics_and_logs(
        self,
    ) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock()

        sqs = MagicMock()
        sqs.delete_message = MagicMock()

        metrics = MagicMock()
        batcher = _make_batcher(
            processor=processor,
            sqs_client=sqs,
            flush_max_messages=1,
            scribe_metrics=metrics,
        )
        await batcher.start()

        await batcher.add(_make_sqs_message(redrive_run_id="run-1"))
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        metrics.record_batch_flush.assert_called_once_with(
            "incidentio", "base", 1, ANY, "success", redriven=True
        )
        sqs.delete_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_of_fresh_group_tags_metrics_as_not_redriven(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock()

        metrics = MagicMock()
        batcher = _make_batcher(
            processor=processor, flush_max_messages=1, scribe_metrics=metrics
        )
        await batcher.start()

        await batcher.add(_make_sqs_message())
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        metrics.record_batch_flush.assert_called_once_with(
            "incidentio", "base", 1, ANY, "success", redriven=False
        )

    @pytest.mark.asyncio
    async def test_group_failure_of_redriven_group_tags_metrics(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock(side_effect=RuntimeError("db error"))

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()

        metrics = MagicMock()
        batcher = _make_batcher(
            processor=processor,
            sqs_client=sqs,
            flush_max_messages=1,
            scribe_metrics=metrics,
        )
        await batcher.start()

        await batcher.add(_make_sqs_message(redrive_run_id="run-1"))
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        metrics.record_group_failure.assert_called_once_with(
            "incidentio", "base", 1, redriven=True
        )

    @pytest.mark.asyncio
    async def test_max_receive_count_dlq_metric_tagged_when_redriven(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            return_value=ValidatedMessage(
                parsed={
                    "source_type": "incidentio",
                    "message_type": "base",
                    "data": {},
                },
                source_type="incidentio",
                message_type="base",
            )
        )
        processor.dispatch_batch = AsyncMock(side_effect=RuntimeError("transient"))

        sqs = MagicMock()
        sqs.change_message_visibility = MagicMock()

        metrics = MagicMock()
        batcher = _make_batcher(
            processor=processor,
            sqs_client=sqs,
            flush_max_messages=1,
            max_receive_count=5,
            scribe_metrics=metrics,
        )
        await batcher.start()

        msg = _make_sqs_message(receive_count="5", redrive_run_id="run-1")
        await batcher.add(msg)
        await asyncio.gather(*list(batcher._inflight), return_exceptions=True)

        metrics.record_dlq_message.assert_called_once_with(
            "incidentio", "RetryableError", redriven=True
        )

    @pytest.mark.asyncio
    async def test_malformed_redriven_message_dlq_metric_tagged(self) -> None:
        processor = MagicMock(spec=ScribeProcessor)
        processor.parse_and_validate = MagicMock(
            side_effect=MalformedMessageError("bad json")
        )
        sqs = MagicMock()
        sqs.send_message = MagicMock()
        sqs.delete_message = MagicMock()

        metrics = MagicMock()
        batcher = _make_batcher(
            processor=processor, sqs_client=sqs, scribe_metrics=metrics
        )
        await batcher.start()

        msg = _make_sqs_message(redrive_run_id="run-1")
        await batcher.add(msg)

        metrics.record_dlq_message.assert_called_once_with(
            "unknown", "MalformedMessageError", redriven=True
        )
