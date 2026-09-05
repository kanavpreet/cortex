"""Unit tests for enricher/main.py — backoff, error classes, consumer loop, bootstrap."""

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import common.llm_tracing.operation as operation_mod
from common.models.enricher_config import EnricherConfig, SourceMappingEntry
from common.models.matik_config import MatikConfig
from enricher.exceptions import (
    ContentFilteredError,
    MalformedMessageError,
    NonRetryableError,
)
from enricher.main import (
    _extract_source_type,
    compute_backoff_with_jitter,
    run_consumer_loop,
    run_enricher,
)


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """In-memory span exporter, wired in place of operation._tracer."""
    provider = TracerProvider()
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    monkeypatch.setattr(operation_mod, "_tracer", provider.get_tracer(__name__))
    yield memory_exporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enricher_config() -> EnricherConfig:
    """Create a minimal EnricherConfig for testing."""
    return EnricherConfig(
        enricher_queue_url="https://sqs.example.com/enricher",
        enricher_dlq_url="https://sqs.example.com/dlq",
        scribe_llm_queue_url="https://sqs.example.com/scribe",
        general_prompt="{source_instructions}",
        source_mappings={
            "incidentio": [
                SourceMappingEntry(
                    input_keys=["summary"],
                    output_field="root_cause_summary",
                    prompt="Root cause prompt.",
                    hash_field="root_cause_summary_hash",
                )
            ]
        },
        max_concurrent_llm_calls=5,
        sqs_max_messages=10,
        sqs_wait_time_seconds=1,
        sqs_poll_error_delay=1,
        visibility_timeout_seconds=30,
    )


def _make_matik_config() -> MatikConfig:
    """Create a minimal MatikConfig with enricher config populated."""
    return MatikConfig(enricher=_make_enricher_config())


def _make_sqs_message(
    body: str,
    receive_count: int = 1,
    receipt_handle: str = "rh-abc",
) -> dict[str, Any]:
    """Create a mock SQS message dict.

    Args:
        body: JSON body string.
        receive_count: ApproximateReceiveCount attribute value.
        receipt_handle: SQS receipt handle string.
    """
    return {
        "Body": body,
        "ReceiptHandle": receipt_handle,
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
    }


def _valid_message_body(source_type: str = "incidentio") -> str:
    """Return a valid JSON EnrichmentRequest message body."""
    return json.dumps(
        {
            "source_type": source_type,
            "producer": "historian",
            "task_id": "t-1",
            "entity_id": {"incident_id": "INC-1"},
            "content": {"summary": "DB overload"},
        }
    )


# ---------------------------------------------------------------------------
# Error class hierarchy tests
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """Tests for the enricher error class hierarchy."""

    def test_non_retryable_error_is_exception(self) -> None:
        """NonRetryableError is an Exception subclass."""
        assert issubclass(NonRetryableError, Exception)

    def test_malformed_message_error_is_non_retryable(self) -> None:
        """MalformedMessageError inherits from NonRetryableError."""
        assert issubclass(MalformedMessageError, NonRetryableError)

    def test_content_filtered_error_is_non_retryable(self) -> None:
        """ContentFilteredError inherits from NonRetryableError."""
        assert issubclass(ContentFilteredError, NonRetryableError)

    def test_malformed_message_is_not_content_filtered(self) -> None:
        """MalformedMessageError is not a subclass of ContentFilteredError."""
        assert not issubclass(MalformedMessageError, ContentFilteredError)


# ---------------------------------------------------------------------------
# Backoff tests
# ---------------------------------------------------------------------------


class TestComputeBackoffWithJitter:
    """Tests for compute_backoff_with_jitter backoff tiers and jitter bounds."""

    def test_receive_count_1_uses_tier_0(self) -> None:
        """receive_count=1 maps to 10s base with ±20% jitter."""
        results = [compute_backoff_with_jitter(1) for _ in range(100)]
        assert all(8.0 <= v <= 12.0 for v in results), f"Values out of range: {results}"

    def test_receive_count_2_uses_tier_1(self) -> None:
        """receive_count=2 maps to 30s base with ±20% jitter."""
        results = [compute_backoff_with_jitter(2) for _ in range(100)]
        assert all(24.0 <= v <= 36.0 for v in results), (
            f"Values out of range: {results}"
        )

    def test_receive_count_3_uses_tier_2(self) -> None:
        """receive_count=3 maps to 60s base with ±20% jitter."""
        results = [compute_backoff_with_jitter(3) for _ in range(100)]
        assert all(48.0 <= v <= 72.0 for v in results), (
            f"Values out of range: {results}"
        )

    def test_receive_count_beyond_tiers_uses_max(self) -> None:
        """receive_count beyond tier count uses the maximum (60s) tier."""
        results = [compute_backoff_with_jitter(99) for _ in range(100)]
        assert all(48.0 <= v <= 72.0 for v in results), (
            f"Values out of range: {results}"
        )

    def test_backoff_is_non_negative(self) -> None:
        """Backoff is always non-negative."""
        for receive_count in range(1, 10):
            assert compute_backoff_with_jitter(receive_count) >= 0.0


# ---------------------------------------------------------------------------
# _extract_source_type tests
# ---------------------------------------------------------------------------


class TestExtractSourceType:
    """Tests for _extract_source_type helper."""

    def test_extracts_valid_source_type(self) -> None:
        """Extracts source_type from valid JSON body."""
        body = json.dumps({"source_type": "incidentio", "other": "data"})
        assert _extract_source_type(body) == "incidentio"

    def test_returns_unknown_for_invalid_json(self) -> None:
        """Returns 'unknown' for non-JSON body."""
        assert _extract_source_type("not json") == "unknown"

    def test_returns_unknown_for_missing_source_type(self) -> None:
        """Returns 'unknown' when source_type key is absent."""
        body = json.dumps({"other": "data"})
        assert _extract_source_type(body) == "unknown"

    def test_returns_unknown_for_empty_string(self) -> None:
        """Returns 'unknown' for empty string."""
        assert _extract_source_type("") == "unknown"


# ---------------------------------------------------------------------------
# Consumer loop tests
# ---------------------------------------------------------------------------


class TestRunConsumerLoop:
    """Tests for the run_consumer_loop function."""

    async def test_success_path_deletes_message_and_records_metrics(self) -> None:
        """Successful processing deletes message and records processed metric."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        enricher_metrics = MagicMock()
        mock_record = MagicMock()
        enricher_metrics.start_message.return_value = mock_record

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        processor.process.assert_awaited_once()
        sqs.delete_message.assert_awaited_once()
        mock_record.assert_called_once_with(True, None)

    async def test_opens_named_span_for_unparsed_message(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """The fallback (unparsed) path still opens a root span, without entity_id."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        msg = _make_sqs_message("not valid json at all")
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "enricher_process_message"
        assert span.attributes is not None
        assert span.attributes["source"] == "unknown"
        assert "incident_id" not in span.attributes

    async def test_named_span_carries_entity_id_on_fast_path(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """The pre-parsed fast path tags the span with the request's entity_id."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        handler = MagicMock()
        handler.batch_fetch_hashes = AsyncMock(
            return_value={
                '{"incident_id": "INC-1"}': {"root_cause_summary_hash": "abc"}
            }
        )
        handler.enrich = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
            handler=handler,
        )

        (span,) = span_exporter.get_finished_spans()
        assert span.name == "enricher_process_message"
        assert span.attributes is not None
        assert span.attributes["incident_id"] == "INC-1"

    async def test_non_retryable_error_routes_to_dlq(self) -> None:
        """NonRetryableError causes message to be sent to DLQ and deleted."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=MalformedMessageError("bad json"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        enricher_metrics = MagicMock()
        enricher_metrics.start_message.return_value = MagicMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        # Message sent to DLQ and deleted from main queue
        sqs.send_message.assert_awaited_once()
        assert (
            sqs.send_message.call_args[1]["queue_url"] == "https://sqs.example.com/dlq"
        )
        sqs.delete_message.assert_awaited_once()
        enricher_metrics.record_dlq.assert_called_once()

    async def test_retryable_error_applies_backoff(self) -> None:
        """Retryable exceptions trigger change_message_visibility with backoff."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=ConnectionError("network timeout"))

        msg = _make_sqs_message(_valid_message_body(), receive_count=1)
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.change_message_visibility = AsyncMock()
        sqs.delete_message = AsyncMock()

        enricher_metrics = MagicMock()
        enricher_metrics.start_message.return_value = MagicMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        # Visibility changed for backoff, message NOT deleted
        sqs.change_message_visibility.assert_awaited_once()
        sqs.delete_message.assert_not_awaited()
        enricher_metrics.record_backoff.assert_called_once()

    async def test_backpressure_caps_in_flight_messages(self) -> None:
        """The loop stops receiving once max_in_flight messages are in flight
        (max_concurrent_llm_calls + one receive batch), instead of draining the
        whole queue into memory and starting every visibility timer at once."""
        config = _make_matik_config()
        assert config.enricher is not None
        config.enricher.max_concurrent_llm_calls = 2
        config.enricher.sqs_max_messages = 1  # one msg per receive -> easy counting
        # max_in_flight = max_concurrent_llm_calls + sqs_max_messages = 3
        shutdown_event = asyncio.Event()

        gate = asyncio.Event()  # processing blocks here until released

        async def blocking_process(body: str) -> None:
            await gate.wait()

        processor = MagicMock()
        processor.process = blocking_process

        sqs = MagicMock()
        receive_calls = 0
        max_messages_seen: list[int] = []

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal receive_calls
            receive_calls += 1
            max_messages_seen.append(kwargs["max_messages"])
            return [
                _make_sqs_message(
                    _valid_message_body(), receipt_handle=f"rh-{receive_calls}"
                )
            ]

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        loop_task = asyncio.create_task(
            run_consumer_loop(
                config=config,
                sqs_client=sqs,
                processor=processor,
                enricher_metrics=None,
                shutdown_event=shutdown_event,
                handler=None,
            )
        )

        # Let the loop receive until it hits the in-flight cap and blocks.
        await asyncio.sleep(0.1)

        # Backpressure: receiving halts at max_in_flight (=3), not unbounded.
        assert receive_calls == 3
        # And it never requests more than the configured batch size.
        assert all(n <= 1 for n in max_messages_seen)

        # Release processing and shut down; the loop should drain cleanly.
        shutdown_event.set()
        gate.set()
        await asyncio.wait_for(loop_task, timeout=2.0)

    async def test_max_receive_count_records_dlq_and_lets_sqs_redrive(self) -> None:
        """At max_receive_count, a retryable error records a DLQ metric and lets
        SQS redrive: no visibility extension, no delete, no app-side DLQ send."""
        config = _make_matik_config()
        assert config.enricher is not None
        config.enricher.max_receive_count = 3
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=ConnectionError("network timeout"))

        # ApproximateReceiveCount == max_receive_count -> final delivery attempt
        msg = _make_sqs_message(_valid_message_body(), receive_count=3)
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.change_message_visibility = AsyncMock()
        sqs.delete_message = AsyncMock()
        sqs.send_message = AsyncMock()

        enricher_metrics = MagicMock()
        enricher_metrics.start_message.return_value = MagicMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        # No visibility extension or backoff — SQS will redrive on its own.
        sqs.change_message_visibility.assert_not_awaited()
        enricher_metrics.record_backoff.assert_not_called()
        # Do NOT delete or re-send: deleting would prevent SQS from redriving,
        # and SQS (not the app) is responsible for moving it to the DLQ.
        sqs.delete_message.assert_not_awaited()
        sqs.send_message.assert_not_awaited()
        # A DLQ metric is recorded with the originating error before SQS redrives.
        enricher_metrics.record_dlq.assert_called_once()
        assert enricher_metrics.record_dlq.call_args[0][0] == "incidentio"
        assert isinstance(enricher_metrics.record_dlq.call_args[0][1], ConnectionError)

    async def test_sqs_poll_error_retries_after_delay(self) -> None:
        """SQS poll error sleeps for sqs_poll_error_delay and retries."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("SQS unavailable")
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)

        with patch("enricher.main.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await run_consumer_loop(
                config=config,
                sqs_client=sqs,
                processor=processor,
                enricher_metrics=None,
                shutdown_event=shutdown_event,
            )
            mock_sleep.assert_awaited_once()

    async def test_facade_bad_request_routes_to_dlq(self) -> None:
        """FacadeBadRequestError also routes to DLQ (non-retryable)."""
        from common.clients.facade_client import FacadeBadRequestError

        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=FacadeBadRequestError("bad prompt"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        enricher_metrics = MagicMock()
        enricher_metrics.start_message.return_value = MagicMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        sqs.send_message.assert_awaited_once()
        assert (
            sqs.send_message.call_args[1]["queue_url"] == "https://sqs.example.com/dlq"
        )


# ---------------------------------------------------------------------------
# poll_queue_depth tests
# ---------------------------------------------------------------------------


class TestPollQueueDepth:
    """Tests for the poll_queue_depth background task."""

    async def test_records_depth_for_both_queues(self) -> None:
        """poll_queue_depth records queue_depth for both 'enricher' and 'dlq' labels."""
        from common.utils.queue_utils import poll_queue_depth

        config = _make_matik_config()
        shutdown_event = asyncio.Event()
        sqs = MagicMock()
        sqs.get_queue_attributes = AsyncMock(
            return_value={"ApproximateNumberOfMessages": "7"}
        )
        enricher_metrics = MagicMock()
        call_count = 0

        async def patched_wait_for(coro: Any, timeout: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                shutdown_event.set()
            coro.close()
            raise TimeoutError

        with patch(
            "common.utils.queue_utils.asyncio.wait_for", side_effect=patched_wait_for
        ):
            await poll_queue_depth(
                sqs_client=sqs,
                config=config,
                enricher_metrics=enricher_metrics,
                shutdown_event=shutdown_event,
                interval=0,
            )

        calls = [c[0] for c in enricher_metrics.record_queue_depth.call_args_list]
        queue_labels = [c[0] for c in calls]
        assert "enricher" in queue_labels
        assert "dlq" in queue_labels

    async def test_swallows_attribute_polling_errors(self) -> None:
        """poll_queue_depth does not propagate exceptions from get_queue_attributes."""
        from common.utils.queue_utils import poll_queue_depth

        config = _make_matik_config()
        shutdown_event = asyncio.Event()
        sqs = MagicMock()
        sqs.get_queue_attributes = AsyncMock(side_effect=ConnectionError("SQS down"))
        enricher_metrics = MagicMock()

        async def patched_wait_for(coro: Any, timeout: float) -> None:
            shutdown_event.set()
            coro.close()
            raise TimeoutError

        with patch(
            "common.utils.queue_utils.asyncio.wait_for", side_effect=patched_wait_for
        ):
            # Must not raise despite get_queue_attributes failing
            await poll_queue_depth(
                sqs_client=sqs,
                config=config,
                enricher_metrics=enricher_metrics,
                shutdown_event=shutdown_event,
                interval=0,
            )

        # No depth was recorded because all calls errored
        enricher_metrics.record_queue_depth.assert_not_called()

    async def test_stops_when_shutdown_event_is_set(self) -> None:
        """poll_queue_depth exits its loop when shutdown_event is set."""
        from common.utils.queue_utils import poll_queue_depth

        config = _make_matik_config()
        shutdown_event = asyncio.Event()
        sqs = MagicMock()
        sqs.get_queue_attributes = AsyncMock(
            return_value={"ApproximateNumberOfMessages": "0"}
        )
        enricher_metrics = MagicMock()

        # Set the event before we even start so the loop exits immediately
        shutdown_event.set()

        await poll_queue_depth(
            sqs_client=sqs,
            config=config,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
            interval=0,
        )

        # Loop exited — no iterations happened after shutdown was set
        enricher_metrics.record_queue_depth.assert_not_called()


class TestConsumerLoopAdditional:
    """Additional consumer loop tests for edge cases."""

    async def test_content_filtered_error_routes_to_dlq(self) -> None:
        """ContentFilteredError (NonRetryableError subclass) routes to DLQ."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=ContentFilteredError("blocked"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        enricher_metrics = MagicMock()
        enricher_metrics.start_message.return_value = MagicMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=enricher_metrics,
            shutdown_event=shutdown_event,
        )

        sqs.send_message.assert_awaited_once()
        assert (
            sqs.send_message.call_args[1]["queue_url"] == "https://sqs.example.com/dlq"
        )
        enricher_metrics.record_dlq.assert_called_once()

    async def test_no_crash_when_enricher_metrics_is_none_success(self) -> None:
        """Consumer loop does not crash on success path when enricher_metrics=None."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        # Must not raise with enricher_metrics=None
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        processor.process.assert_awaited_once()
        sqs.delete_message.assert_awaited_once()

    async def test_no_crash_when_enricher_metrics_is_none_error(self) -> None:
        """Consumer loop does not crash on error path when enricher_metrics=None."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=MalformedMessageError("bad"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        # Must not raise with enricher_metrics=None
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        sqs.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# Batch fetch integration tests
# ---------------------------------------------------------------------------


class TestConsumerLoopBatchFetch:
    """Tests for the pre-parse + batch-fetch phase in run_consumer_loop."""

    async def test_consumer_loop_batch_fetches_before_dispatch(self) -> None:
        """Consumer loop calls handler.batch_fetch_hashes before dispatching tasks."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        handler = MagicMock()
        handler.batch_fetch_hashes = AsyncMock(
            return_value={
                '{"incident_id": "INC-1"}': {"root_cause_summary_hash": "abc"}
            }
        )
        handler.enrich = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
            handler=handler,
        )

        handler.batch_fetch_hashes.assert_awaited_once()
        handler.enrich.assert_awaited_once()
        # processor.process should NOT be called since message was parseable
        processor.process.assert_not_awaited()

    async def test_consumer_loop_unparseable_message_uses_processor(self) -> None:
        """Unparseable messages bypass batch-fetch and fall back to processor.process()."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        handler = MagicMock()
        handler.batch_fetch_hashes = AsyncMock(return_value={})
        handler.enrich = AsyncMock()

        bad_body = "not valid json at all"
        msg = _make_sqs_message(bad_body)
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
            handler=handler,
        )

        # Unparseable message goes to processor (which will raise MalformedMessageError -> DLQ)
        processor.process.assert_awaited_once()
        handler.enrich.assert_not_awaited()

    async def test_consumer_loop_batch_fetch_failure_falls_back(self) -> None:
        """When batch_fetch_hashes raises, consumer loop continues with empty pre-fetched hashes."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        handler = MagicMock()
        handler.batch_fetch_hashes = AsyncMock(side_effect=ConnectionError("API down"))
        handler.enrich = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.delete_message = AsyncMock()

        # Should not raise even though batch_fetch_hashes failed
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
            handler=handler,
        )

        # enrich is still called (with None pre-hashes as fallback)
        handler.enrich.assert_awaited_once()
        _, call_kwargs = handler.enrich.call_args
        assert call_kwargs.get("existing_hashes") is None


# ---------------------------------------------------------------------------
# Consumer loop exception handler edge cases
# ---------------------------------------------------------------------------


class TestConsumerLoopExceptionHandlers:
    """Tests for inner exception handlers in the consumer loop."""

    async def test_dlq_send_failure_is_swallowed(self) -> None:
        """If sending to DLQ raises, the error is logged but not re-raised."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=MalformedMessageError("bad"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock(side_effect=RuntimeError("DLQ unavailable"))
        sqs.delete_message = AsyncMock()

        # Should not raise even though send_message fails
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        sqs.send_message.assert_awaited_once()

    async def test_delete_after_dlq_failure_is_swallowed(self) -> None:
        """If delete after DLQ send raises, the error is logged but not re-raised."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=MalformedMessageError("bad"))

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock(side_effect=RuntimeError("delete failed"))

        # Should not raise even though delete_message fails
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        sqs.delete_message.assert_awaited_once()

    async def test_visibility_change_failure_is_swallowed(self) -> None:
        """If change_message_visibility raises during backoff, error is swallowed."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=ConnectionError("network error"))

        msg = _make_sqs_message(_valid_message_body(), receive_count=1)
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.change_message_visibility = AsyncMock(
            side_effect=RuntimeError("visibility failed")
        )

        # Should not raise even though change_message_visibility fails
        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        sqs.change_message_visibility.assert_awaited_once()

    async def test_unknown_source_type_falls_through_to_processor(self) -> None:
        """Message with valid JSON but unconfigured source_type falls back to processor."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock(side_effect=MalformedMessageError("unknown type"))

        # valid JSON + valid schema, but source_type not in config (only "incidentio")
        body = json.dumps(
            {
                "source_type": "pagerduty",
                "producer": "historian",
                "task_id": "t-1",
                "entity_id": {"alert_id": "A1"},
                "content": {"summary": "disk full"},
            }
        )
        msg = _make_sqs_message(body)
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)
        sqs.send_message = AsyncMock()
        sqs.delete_message = AsyncMock()

        await run_consumer_loop(
            config=config,
            sqs_client=sqs,
            processor=processor,
            enricher_metrics=None,
            shutdown_event=shutdown_event,
        )

        # Fell through to processor which raised MalformedMessageError -> DLQ
        sqs.send_message.assert_awaited_once()

    async def test_unhandled_task_exception_is_logged(self) -> None:
        """An exception escaping _process (e.g. from start_message, which runs
        outside the try) is logged by the done-callback, not silently dropped."""
        config = _make_matik_config()
        shutdown_event = asyncio.Event()

        sqs = MagicMock()
        processor = MagicMock()
        processor.process = AsyncMock()

        msg = _make_sqs_message(_valid_message_body())
        call_count = 0

        async def receive_side_effect(**kwargs: Any) -> list[dict[str, Any]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [msg]
            shutdown_event.set()
            return []

        sqs.receive_messages = AsyncMock(side_effect=receive_side_effect)

        # start_message() is called outside the inner try, so a failure here
        # escapes _process entirely and can only be caught by the done-callback.
        enricher_metrics = MagicMock()
        enricher_metrics.start_message.side_effect = RuntimeError("metrics boom")

        with patch("enricher.main.logger") as mock_logger:
            await run_consumer_loop(
                config=config,
                sqs_client=sqs,
                processor=processor,
                enricher_metrics=enricher_metrics,
                shutdown_event=shutdown_event,
            )
            await asyncio.sleep(0)  # flush any pending done-callbacks

        # The done-callback surfaced the unhandled exception instead of swallowing it.
        error_messages = [c.args[0] for c in mock_logger.error.call_args_list]
        assert "Enrichment task failed with an unhandled exception" in error_messages
        # Processing never ran because start_message raised first.
        processor.process.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------


class TestRunEnricher:
    """Tests for run_enricher bootstrap."""

    async def test_missing_enricher_config_returns_1(self) -> None:
        """run_enricher returns 1 when config.enricher is None."""
        config = MatikConfig()
        result = await run_enricher(config)
        assert result == 1

    async def test_missing_facade_config_returns_1(self) -> None:
        """run_enricher returns 1 when config.facade is None."""
        config = MatikConfig(enricher=_make_enricher_config())
        # facade is None by default
        result = await run_enricher(config)
        assert result == 1

    async def test_success_path_returns_0(self) -> None:
        """run_enricher returns 0 when config is valid and consumer loop completes."""
        from common.models.facade_config import FacadeConfig

        config = MatikConfig(
            enricher=_make_enricher_config(),
            facade=FacadeConfig(
                base_url="http://facade.example.com",
                resource_bucket="test",
                default_model="gpt-4o",
                api_version="2024-01-01",
            ),
        )
        with (
            patch(
                "common.clients.facade_client.create_facade_client",
                return_value=MagicMock(),
            ),
            patch("common.queues.sqs_client.SQSClient"),
            patch("enricher.hash_cache.HashCache"),
            patch("enricher.handlers.enrichment_handler.EnrichmentHandler"),
            patch("enricher.processor.EnricherProcessor"),
            patch("enricher.main.run_consumer_loop", new_callable=AsyncMock),
        ):
            result = await run_enricher(config)
        assert result == 0

    async def test_with_api_config_creates_api_client(self) -> None:
        """run_enricher creates the Matik API client when api config is present."""
        from common.models.api_config import ApiConfig
        from common.models.facade_config import FacadeConfig

        config = MatikConfig(
            enricher=_make_enricher_config(),
            facade=FacadeConfig(
                base_url="http://facade.example.com",
                resource_bucket="test",
                default_model="gpt-4o",
                api_version="2024-01-01",
            ),
            api=ApiConfig(api_endpoint="http://matik-api.example.com"),
        )
        with (
            patch(
                "common.clients.facade_client.create_facade_client",
                return_value=MagicMock(),
            ),
            patch("common.queues.sqs_client.SQSClient"),
            patch("enricher.hash_cache.HashCache"),
            patch("enricher.handlers.enrichment_handler.EnrichmentHandler"),
            patch("enricher.processor.EnricherProcessor"),
            patch("enricher.main.run_consumer_loop", new_callable=AsyncMock),
            patch(
                "common.clients.matik_api_client.create_matik_api_client",
                return_value=MagicMock(),
            ) as mock_create_api,
        ):
            result = await run_enricher(config)
        assert result == 0
        mock_create_api.assert_called_once()

    async def test_telescope_enabled_initializes_metrics_and_shuts_down(self) -> None:
        """run_enricher starts and stops telescope when telescope config is enabled."""
        from common.models.facade_config import FacadeConfig
        from common.models.telescope_config import TelescopeConfig

        config = MatikConfig(
            enricher=_make_enricher_config(),
            facade=FacadeConfig(
                base_url="http://facade.example.com",
                resource_bucket="test",
                default_model="gpt-4o",
                api_version="2024-01-01",
            ),
            telescope=TelescopeConfig(enabled=True, tenant_id="matik"),
        )
        mock_telescope = MagicMock()
        mock_telescope.meter = MagicMock()
        with (
            patch("enricher.main.TelescopeClient", return_value=mock_telescope),
            patch(
                "common.clients.facade_client.create_facade_client",
                return_value=MagicMock(),
            ),
            patch("common.queues.sqs_client.SQSClient"),
            patch("enricher.hash_cache.HashCache"),
            patch("enricher.handlers.enrichment_handler.EnrichmentHandler"),
            patch("enricher.processor.EnricherProcessor"),
            patch("enricher.main.run_consumer_loop", new_callable=AsyncMock),
            patch("enricher.main.poll_queue_depth", new_callable=AsyncMock),
        ):
            result = await run_enricher(config)
        assert result == 0
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()

    async def test_missing_facade_with_telescope_shuts_down_telescope(self) -> None:
        """run_enricher shuts down telescope before returning 1 when facade is missing."""
        from common.models.telescope_config import TelescopeConfig

        config = MatikConfig(
            enricher=_make_enricher_config(),
            telescope=TelescopeConfig(enabled=True, tenant_id="matik"),
        )
        mock_telescope = MagicMock()
        mock_telescope.meter = MagicMock()
        with patch("enricher.main.TelescopeClient", return_value=mock_telescope):
            result = await run_enricher(config)
        assert result == 1
        mock_telescope.shutdown.assert_called_once()

    async def test_uses_bedrock_client_when_llm_provider_is_bedrock(self) -> None:
        """run_enricher creates a BedrockClient when llm_provider is bedrock."""
        from common.models.bedrock_config import BedrockConfig

        enricher_cfg = _make_enricher_config()
        enricher_cfg.llm_provider = "bedrock"
        config = MatikConfig(
            enricher=enricher_cfg,
            bedrock=BedrockConfig(
                base_url="http://bedrock.example.com",
                region="us-east-1",
                default_model="anthropic.claude",
            ),
        )
        with (
            patch(
                "common.clients.bedrock_client.create_bedrock_client",
                return_value=MagicMock(),
            ) as mock_create_bedrock,
            patch(
                "common.clients.facade_client.create_facade_client"
            ) as mock_create_facade,
            patch("common.queues.sqs_client.SQSClient"),
            patch("enricher.hash_cache.HashCache"),
            patch("enricher.handlers.enrichment_handler.EnrichmentHandler"),
            patch("enricher.processor.EnricherProcessor"),
            patch("enricher.main.run_consumer_loop", new_callable=AsyncMock),
        ):
            result = await run_enricher(config)
        assert result == 0
        mock_create_bedrock.assert_called_once()
        mock_create_facade.assert_not_called()

    async def test_missing_bedrock_config_returns_1(self) -> None:
        """run_enricher returns 1 when llm_provider is bedrock but bedrock is None."""
        enricher_cfg = _make_enricher_config()
        enricher_cfg.llm_provider = "bedrock"
        config = MatikConfig(enricher=enricher_cfg)
        # bedrock is None by default
        result = await run_enricher(config)
        assert result == 1

    async def test_missing_facade_with_tracing_shuts_down_tracing(self) -> None:
        """run_enricher shuts down tracing before returning 1 when facade is missing."""
        from common.models.llm_tracing_config import LLMTracingConfig

        config = MatikConfig(
            enricher=_make_enricher_config(),
            llm_tracing=LLMTracingConfig(enabled=True, braintrust_project_id="p"),
        )
        mock_tracing = MagicMock()
        with patch("enricher.main.LLMTracingClient", return_value=mock_tracing):
            result = await run_enricher(config)
        assert result == 1
        mock_tracing.shutdown.assert_called_once()

    async def test_missing_bedrock_with_tracing_shuts_down_tracing(self) -> None:
        """run_enricher shuts down tracing before returning 1 when bedrock is missing."""
        from common.models.llm_tracing_config import LLMTracingConfig

        enricher_cfg = _make_enricher_config()
        enricher_cfg.llm_provider = "bedrock"
        config = MatikConfig(
            enricher=enricher_cfg,
            llm_tracing=LLMTracingConfig(enabled=True, braintrust_project_id="p"),
        )
        mock_tracing = MagicMock()
        with patch("enricher.main.LLMTracingClient", return_value=mock_tracing):
            result = await run_enricher(config)
        assert result == 1
        mock_tracing.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# main() bootstrap tests
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point."""

    async def test_main_success_calls_run_enricher(self) -> None:
        """main() loads configs and delegates to run_enricher."""
        from enricher.main import main

        mock_config = MagicMock()
        with (
            patch("enricher.main.load_config", return_value=mock_config),
            patch("enricher.main.merge_config", return_value=mock_config),
            patch("enricher.main.run_enricher", new_callable=AsyncMock, return_value=0),
        ):
            result = await main()
        assert result == 0

    async def test_main_returns_1_when_enricher_config_load_fails(self) -> None:
        """main() returns 1 if matik-enricher-config.yml cannot be loaded."""
        from enricher.main import main

        with patch(
            "enricher.main.load_config", side_effect=FileNotFoundError("not found")
        ):
            result = await main()
        assert result == 1

    async def test_main_returns_1_when_facade_config_load_fails(self) -> None:
        """main() returns 1 if facade-config.yml cannot be loaded."""
        from enricher.main import main

        mock_config = MagicMock()

        def merge_side_effect(config: Any, filename: str) -> Any:
            if "facade" in filename:
                raise FileNotFoundError("not found")
            return config

        with (
            patch("enricher.main.load_config", return_value=mock_config),
            patch("enricher.main.merge_config", side_effect=merge_side_effect),
        ):
            result = await main()
        assert result == 1
