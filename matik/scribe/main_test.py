"""Tests for Scribe service main module."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scribe.main import (
    BACKOFF_DELAYS,
    _create_sqs_client,
    _extract_field,
    _poll_sqs,
    _run_single_queue_loop,
    compute_backoff_with_jitter,
    main,
)


@pytest.fixture(autouse=True)
def no_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace asyncio.to_thread with a direct coroutine call to avoid thread overhead."""

    async def fake_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    monkeypatch.setattr("scribe.main.asyncio.to_thread", fake_to_thread)


def _default_poll_cfg() -> dict[str, Any]:
    return {
        "sqs_max_messages": 10,
        "sqs_wait_time_seconds": 20,
        "sqs_visibility_timeout": 300,
        "sqs_poll_error_delay": 0,
    }


def _make_sqs_message(
    body: dict[str, Any],
    receipt_handle: str = "rh-1",
    receive_count: str = "1",
) -> dict[str, Any]:
    return {
        "Body": json.dumps(body),
        "ReceiptHandle": receipt_handle,
        "Attributes": {"ApproximateReceiveCount": receive_count},
    }


def _make_queue(
    queue_url: str = "https://sqs.example.com/queue",
    dlq_url: str = "https://sqs.example.com/dlq",
) -> Any:
    """Build a ScribeQueueConfig-like object for tests."""
    from common.models.scribe_config import ScribeQueueConfig

    return ScribeQueueConfig(
        queue_url=queue_url,
        dlq_url=dlq_url,
    )


# =============================================================================
# BACKOFF_DELAYS constant
# =============================================================================


class TestBackoffDelays:
    def test_delays_defined(self) -> None:
        assert BACKOFF_DELAYS == [10, 30, 60]

    def test_delays_ascending(self) -> None:
        for i in range(len(BACKOFF_DELAYS) - 1):
            assert BACKOFF_DELAYS[i] < BACKOFF_DELAYS[i + 1]


# =============================================================================
# compute_backoff_with_jitter
# =============================================================================


class TestComputeBackoffWithJitter:
    def test_first_attempt_in_range(self) -> None:
        """First attempt uses base delay of 10s with +-20% jitter (8-12s)."""
        for _ in range(20):
            result = compute_backoff_with_jitter(receive_count=1)
            assert 8 <= result <= 12

    def test_second_attempt_in_range(self) -> None:
        """Second attempt uses base delay of 30s with +-20% jitter (24-36s)."""
        for _ in range(20):
            result = compute_backoff_with_jitter(receive_count=2)
            assert 24 <= result <= 36

    def test_third_and_beyond_capped(self) -> None:
        """Third+ attempt uses max delay of 60s with +-20% jitter (48-72s)."""
        for receive_count in (3, 5, 10, 100):
            result = compute_backoff_with_jitter(receive_count=receive_count)
            assert 48 <= result <= 72

    def test_minimum_is_one(self) -> None:
        """Result is always at least 1 second."""
        for _ in range(50):
            assert compute_backoff_with_jitter(1) >= 1


# =============================================================================
# _extract_field
# =============================================================================


class TestExtractField:
    def test_extracts_present_field(self) -> None:
        body = json.dumps({"source_type": "incidentio", "message_type": "base"})
        assert _extract_field(body, "source_type") == "incidentio"
        assert _extract_field(body, "message_type") == "base"

    def test_missing_field_returns_unknown(self) -> None:
        body = json.dumps({"source_type": "incidentio"})
        assert _extract_field(body, "message_type") == "unknown"

    def test_invalid_json_returns_unknown(self) -> None:
        assert _extract_field("not-json", "source_type") == "unknown"

    def test_none_value_returns_unknown(self) -> None:
        body = json.dumps({"source_type": None})
        assert _extract_field(body, "source_type") == "unknown"

    def test_empty_body_returns_unknown(self) -> None:
        assert _extract_field("", "source_type") == "unknown"


# =============================================================================
# _create_sqs_client
# =============================================================================


class TestCreateSQSClient:
    def test_creates_client_with_region(self) -> None:
        with patch("scribe.main.boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_sqs_client("us-west-2")
            mock_session_cls.assert_called_once_with(region_name="us-west-2")
            call_args = mock_session.client.call_args
            assert call_args[0][0] == "sqs"
            assert call_args[1]["config"].max_pool_connections == 50

    def test_creates_client_with_us_east_1(self) -> None:
        with patch("scribe.main.boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_sqs_client("us-east-1")
            mock_session_cls.assert_called_once_with(region_name="us-east-1")

    def test_custom_pool_size(self) -> None:
        with patch("scribe.main.boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_sqs_client("us-east-1", max_pool_connections=25)
            call_args = mock_session.client.call_args
            assert call_args[1]["config"].max_pool_connections == 25


# =============================================================================
# _poll_sqs
# =============================================================================


class TestPollSQS:
    @pytest.mark.asyncio
    async def test_returns_true_when_messages_received(self) -> None:
        """Returns True when SQS returns at least one message."""
        sqs_client = MagicMock()
        sqs_client.receive_message.return_value = {
            "Messages": [
                _make_sqs_message({"source_type": "incidentio", "message_type": "base"})
            ]
        }

        batcher = MagicMock()
        batcher.add = AsyncMock()

        result = await _poll_sqs(
            sqs_client,
            batcher,
            "queue-url",
            _default_poll_cfg(),
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_queue_empty(self) -> None:
        """Returns False when SQS returns no messages."""
        sqs_client = MagicMock()
        sqs_client.receive_message.return_value = {"Messages": []}

        batcher = MagicMock()
        batcher.add = AsyncMock()

        result = await _poll_sqs(
            sqs_client,
            batcher,
            "queue-url",
            _default_poll_cfg(),
        )

        assert result is False
        batcher.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_called_per_message(self) -> None:
        """batcher.add is called once for each received SQS message."""
        sqs_client = MagicMock()
        sqs_client.receive_message.return_value = {
            "Messages": [
                _make_sqs_message(
                    {"source_type": "incidentio", "message_type": "base"},
                    receipt_handle="rh-1",
                ),
                _make_sqs_message(
                    {"source_type": "jira", "message_type": "base"},
                    receipt_handle="rh-2",
                ),
            ]
        }

        batcher = MagicMock()
        batcher.add = AsyncMock()

        await _poll_sqs(
            sqs_client,
            batcher,
            "queue-url",
            _default_poll_cfg(),
        )

        assert batcher.add.call_count == 2


# =============================================================================
# _run_single_queue_loop
# =============================================================================


class TestRunSingleQueueLoop:
    @pytest.mark.asyncio
    async def test_calls_poll_on_each_iteration(self) -> None:
        """Calls _poll_sqs on each iteration until shutdown_event is set."""
        call_count = 0
        shutdown_event = asyncio.Event()

        async def mock_poll(
            sqs_client: Any,
            batcher: Any,
            queue_url: str,
            poll_cfg: dict[str, Any],
            sqs_metrics: Any = None,
        ) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                shutdown_event.set()
            return True

        batcher = MagicMock()
        batcher.is_overloaded = MagicMock(return_value=False)

        with patch("scribe.main._poll_sqs", side_effect=mock_poll):
            await _run_single_queue_loop(
                sqs_client=MagicMock(),
                batcher=batcher,
                queue_url="queue-url",
                poll_cfg=_default_poll_cfg(),
                shutdown_event=shutdown_event,
            )

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_sleeps_when_queue_empty(self) -> None:
        """Sleeps sqs_poll_error_delay when the queue returns no messages."""
        shutdown_event = asyncio.Event()
        sleep_calls: list[float] = []

        async def mock_poll(*args: Any, **kwargs: Any) -> bool:
            return False

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            shutdown_event.set()

        batcher = MagicMock()
        batcher.is_overloaded = MagicMock(return_value=False)

        with (
            patch("scribe.main._poll_sqs", side_effect=mock_poll),
            patch("scribe.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            await _run_single_queue_loop(
                sqs_client=MagicMock(),
                batcher=batcher,
                queue_url="queue-url",
                poll_cfg=_default_poll_cfg(),
                shutdown_event=shutdown_event,
            )

        assert len(sleep_calls) >= 1

    @pytest.mark.asyncio
    async def test_poll_error_sleeps_and_retries(self) -> None:
        """A poll exception is caught and the loop retries after sleeping."""
        shutdown_event = asyncio.Event()
        call_count = 0
        sleep_calls: list[float] = []

        async def mock_poll(*args: Any, **kwargs: Any) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("SQS connectivity error")
            shutdown_event.set()
            return False

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        batcher = MagicMock()
        batcher.is_overloaded = MagicMock(return_value=False)

        with (
            patch("scribe.main._poll_sqs", side_effect=mock_poll),
            patch("scribe.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            await _run_single_queue_loop(
                sqs_client=MagicMock(),
                batcher=batcher,
                queue_url="queue-url",
                poll_cfg=_default_poll_cfg(),
                shutdown_event=shutdown_event,
            )

        assert len(sleep_calls) >= 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_backpressure_sleep_when_overloaded(self) -> None:
        """Sleeps 50ms and skips polling when batcher.is_overloaded() is True."""
        shutdown_event = asyncio.Event()
        sleep_calls: list[float] = []
        poll_calls = 0

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            shutdown_event.set()

        async def mock_poll(*args: Any, **kwargs: Any) -> bool:
            nonlocal poll_calls
            poll_calls += 1
            return True

        batcher = MagicMock()
        batcher.is_overloaded = MagicMock(return_value=True)

        with (
            patch("scribe.main._poll_sqs", side_effect=mock_poll),
            patch("scribe.main.asyncio.sleep", side_effect=mock_sleep),
        ):
            await _run_single_queue_loop(
                sqs_client=MagicMock(),
                batcher=batcher,
                queue_url="queue-url",
                poll_cfg=_default_poll_cfg(),
                shutdown_event=shutdown_event,
            )

        # Polling was skipped while overloaded
        assert poll_calls == 0
        # Slept with the backpressure delay
        assert 0.05 in sleep_calls

    @pytest.mark.asyncio
    async def test_exits_on_shutdown_event(self) -> None:
        """Loop exits cleanly when shutdown_event is set."""
        shutdown_event = asyncio.Event()
        shutdown_event.set()  # pre-set

        poll_calls = 0

        async def mock_poll(*args: Any, **kwargs: Any) -> bool:
            nonlocal poll_calls
            poll_calls += 1
            return True

        batcher = MagicMock()
        batcher.is_overloaded = MagicMock(return_value=False)

        with patch("scribe.main._poll_sqs", side_effect=mock_poll):
            await _run_single_queue_loop(
                sqs_client=MagicMock(),
                batcher=batcher,
                queue_url="queue-url",
                poll_cfg=_default_poll_cfg(),
                shutdown_event=shutdown_event,
            )

        assert poll_calls == 0


# =============================================================================
# main() startup guards
# =============================================================================


class TestMain:
    @pytest.mark.asyncio
    async def test_returns_1_when_scribe_config_missing(self) -> None:
        """Returns 1 when the scribe config section is absent."""
        mock_config = MagicMock()
        mock_config.scribe = None

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
        ):
            result = await main()

        assert result == 1

    @pytest.mark.asyncio
    async def test_returns_1_when_mysql_config_missing(self) -> None:
        """Returns 1 when the mysql config section is absent."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = None

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
        ):
            result = await main()

        assert result == 1

    @pytest.mark.asyncio
    async def test_starts_polling_when_config_valid(self) -> None:
        """Calls _run_single_queue_loop when all required config is present."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch(
                "scribe.main._run_single_queue_loop", new_callable=AsyncMock
            ) as mock_loop,
        ):
            await main()

        mock_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_polling_uses_queue_from_config(self) -> None:
        """_run_single_queue_loop receives the queue URL from config."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None
        mock_config.enigmatologist = None

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch(
                "scribe.main._run_single_queue_loop", new_callable=AsyncMock
            ) as mock_loop,
        ):
            await main()

        assert (
            mock_loop.call_args[1]["queue_url"] == "https://sqs.example.com/scrb-queue"
        )

    @pytest.mark.asyncio
    async def test_batcher_started_and_stopped(self) -> None:
        """batcher.start() is called before the loop; batcher.stop() in finally."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None
        mock_config.enigmatologist = None

        mock_batcher = MagicMock()
        mock_batcher.start = AsyncMock()
        mock_batcher.stop = AsyncMock()
        mock_batcher.is_overloaded = MagicMock(return_value=False)

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch("scribe.main.ScribeBatcher", return_value=mock_batcher),
            patch("scribe.main._run_single_queue_loop", new_callable=AsyncMock),
        ):
            await main()

        mock_batcher.start.assert_called_once()
        mock_batcher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_enigmatologist_hook_wired_when_configured(self) -> None:
        """EnigmatologistCorrelationHook is registered on the incidentio handler when the SQS URL is set."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None
        mock_config.enigmatologist = MagicMock()
        mock_config.enigmatologist.sqs_queue_url = "https://sqs.example.com/enig-queue"

        captured_processor: list[Any] = []

        async def capture_loop(**kwargs: Any) -> None:
            captured_processor.append(kwargs["batcher"])

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch("scribe.main._run_single_queue_loop", side_effect=capture_loop),
        ):
            await main()

        # The batcher holds the processor; test via the batcher's processor attribute
        batcher = captured_processor[0]
        processor = batcher._processor
        handler = processor._handlers["incidentio"]
        assert len(handler._hooks.base._hooks) == 1
        assert handler._hooks.base._hooks[0].name == "enigmatologist_correlation"
        assert len(handler._hooks.enrichment._hooks) == 1
        assert handler._hooks.enrichment._hooks[0].name == "enigmatologist_correlation"

        # incident_channel_summary shares the same hook instance, enrichment-only
        # (it has no base-write route).
        channel_summary_handler = processor._handlers["incident_channel_summary"]
        assert len(channel_summary_handler._hooks.base._hooks) == 0
        assert len(channel_summary_handler._hooks.enrichment._hooks) == 1
        assert (
            channel_summary_handler._hooks.enrichment._hooks[0].name
            == "enigmatologist_correlation"
        )

    @pytest.mark.asyncio
    async def test_enigmatologist_hook_not_wired_when_not_configured(self) -> None:
        """Incidentio handler uses noop hooks when enigmatologist config is absent."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None
        mock_config.enigmatologist = None

        captured: list[Any] = []

        async def capture_loop(**kwargs: Any) -> None:
            captured.append(kwargs["batcher"])

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch("scribe.main._run_single_queue_loop", side_effect=capture_loop),
        ):
            await main()

        batcher = captured[0]
        processor = batcher._processor
        handler = processor._handlers["incidentio"]
        assert len(handler._hooks.base._hooks) == 0
        assert len(handler._hooks.enrichment._hooks) == 0

        channel_summary_handler = processor._handlers["incident_channel_summary"]
        assert len(channel_summary_handler._hooks.base._hooks) == 0
        assert len(channel_summary_handler._hooks.enrichment._hooks) == 0

    @pytest.mark.asyncio
    async def test_returns_1_when_db_connectivity_check_fails(self) -> None:
        """Returns 1 when the initial SELECT 1 connectivity check raises."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None

        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("connection refused")

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=mock_engine),
        ):
            result = await main()

        assert result == 1

    @pytest.mark.asyncio
    async def test_telescope_shutdown_called_on_exit(self) -> None:
        """telescope.shutdown() is called in the finally block when telescope is enabled."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.enigmatologist = None

        mock_telescope = MagicMock()
        mock_telescope.enabled = True
        mock_telescope.meter = MagicMock()
        mock_config.telescope = mock_telescope

        mock_batcher = MagicMock()
        mock_batcher.start = AsyncMock()
        mock_batcher.stop = AsyncMock()

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch("scribe.main.TelescopeClient", return_value=mock_telescope),
            patch("scribe.main.JobMetrics"),
            patch("scribe.main.DBMetrics"),
            patch("scribe.main.ScribeMetrics"),
            patch("scribe.main.ScribeBatcher", return_value=mock_batcher),
            patch("scribe.main._run_single_queue_loop", new_callable=AsyncMock),
        ):
            await main()

        mock_telescope.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_sigterm_sets_shutdown_event(self) -> None:
        """SIGTERM handler sets the shutdown event, causing the poll loop to exit."""
        from common.models.scribe_config import ScribeConfig, ScribeQueueConfig

        mock_config = MagicMock()
        mock_config.scribe = ScribeConfig(
            queue=ScribeQueueConfig(
                queue_url="https://sqs.example.com/scrb-queue",
                dlq_url="https://sqs.example.com/scrb-dlq",
            )
        )
        mock_config.mysql = MagicMock()
        mock_config.telescope = None
        mock_config.enigmatologist = None

        captured_shutdown: list[asyncio.Event] = []
        registered_handler: list[Any] = []

        mock_loop = MagicMock()
        mock_loop.add_signal_handler = MagicMock(
            side_effect=lambda sig, cb: registered_handler.append(cb)
        )

        async def capture_loop(**kwargs: Any) -> None:
            captured_shutdown.append(kwargs["shutdown_event"])
            # Fire the registered SIGTERM callback
            registered_handler[0]()

        mock_batcher = MagicMock()
        mock_batcher.start = AsyncMock()
        mock_batcher.stop = AsyncMock()

        with (
            patch("scribe.main.load_config", return_value=mock_config),
            patch("scribe.main.log_utils"),
            patch("scribe.main.create_long_lived_engine", return_value=MagicMock()),
            patch("scribe.main._create_sqs_client", return_value=MagicMock()),
            patch("scribe.main.ScribeBatcher", return_value=mock_batcher),
            patch("scribe.main._run_single_queue_loop", side_effect=capture_loop),
            patch("scribe.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            await main()

        assert captured_shutdown[0].is_set()
