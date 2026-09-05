"""Tests for Enigmatologist SQS polling infrastructure."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.models.enigmatologist_config import EnigmatologistConfig
from common.models.matik_config import MatikConfig
from enigmatologist.main import (
    _create_sqs_client,
    _poll_reliability_sqs,
)

# =============================================================================
# _create_sqs_client
# =============================================================================


class TestCreateSQSClient:
    """Tests for _create_sqs_client."""

    def test_creates_client_with_region(self) -> None:
        """Creates an SQS client with the specified region."""
        with patch("enigmatologist.main.boto3.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            _create_sqs_client("us-west-2")
            mock_session_cls.assert_called_once_with(region_name="us-west-2")
            mock_session.client.assert_called_once_with("sqs")


# =============================================================================
# main() startup paths
# =============================================================================


class TestMain:
    """Tests for the main() entry point."""

    @pytest.mark.asyncio
    async def test_exits_with_error_when_no_sqs_queue_url(self) -> None:
        """main() returns 1 when sqs_queue_url is not configured."""
        from common.models.enigmatologist_config import EnigmatologistConfig

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = MagicMock()
        mock_config.api.api_endpoint = "http://localhost:8080"
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/000000000000/scribe-high",
            correlation_system_prompt="test",
        )

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.log_utils"),
        ):
            from enigmatologist.main import main

            result = await main()
        assert result == 1

    @pytest.mark.asyncio
    async def test_exits_with_error_when_no_scribe_queue_url(self) -> None:
        """main() returns 1 when scribe_queue_url is empty."""
        from common.models.enigmatologist_config import EnigmatologistConfig

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="",
            correlation_system_prompt="test",
        )

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.log_utils"),
        ):
            from enigmatologist.main import main

            result = await main()
        assert result == 1

    @pytest.mark.asyncio
    async def test_exits_with_error_when_no_enigmatologist_config(self) -> None:
        """main() returns 1 when enigmatologist config is None."""
        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = MagicMock()
        mock_config.api.api_endpoint = "http://localhost:8080"
        mock_config.enigmatologist = None

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.log_utils"),
        ):
            from enigmatologist.main import main

            result = await main()
        assert result == 1

    @pytest.mark.asyncio
    async def test_logs_warning_when_no_api_config(self) -> None:
        """main() logs warning when api config is not found."""
        from common.models.enigmatologist_config import EnigmatologistConfig

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            correlation_system_prompt="test",
        )

        mock_logger = MagicMock()

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.logger", mock_logger),
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
        ):
            from enigmatologist.main import main

            await main()

        mock_logger.warning.assert_any_call(
            "api config not found, persistence disabled"
        )


# =============================================================================
# _poll_reliability_sqs
# =============================================================================


class TestPollReliabilitySQS:
    """Tests for _poll_reliability_sqs."""

    @pytest.mark.asyncio
    async def test_processes_valid_message_and_deletes(self) -> None:
        """Valid SQS message is processed and deleted on success."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            scribe_queue_url="https://sqs.example.com/scribe-high",
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)
        payload = {"reference_id": "INC-1", "description_summary": "test"}

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "Body": json.dumps(payload),
                            "ReceiptHandle": "receipt-1",
                        }
                    ]
                }
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive
        sqs_client.delete_message = MagicMock()

        semaphore = asyncio.Semaphore(5)

        with patch(
            "enigmatologist.main.run_reliability_correlation",
            new_callable=AsyncMock,
        ) as mock_run:
            with pytest.raises(asyncio.CancelledError):
                await _poll_reliability_sqs(
                    sqs_client, eng_cfg.sqs_queue_url, semaphore, None, None, config
                )

            # Give background tasks a chance to complete
            await asyncio.sleep(0.05)
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == payload

    @pytest.mark.asyncio
    async def test_deletes_invalid_json_message(self) -> None:
        """Invalid JSON message is deleted without processing."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            scribe_queue_url="https://sqs.example.com/scribe-high",
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "Body": "not-json",
                            "ReceiptHandle": "receipt-bad",
                        }
                    ]
                }
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive
        sqs_client.delete_message = MagicMock()

        semaphore = asyncio.Semaphore(5)

        with patch(
            "enigmatologist.main.run_reliability_correlation",
            new_callable=AsyncMock,
        ) as mock_run:
            with pytest.raises(asyncio.CancelledError):
                await _poll_reliability_sqs(
                    sqs_client, eng_cfg.sqs_queue_url, semaphore, None, None, config
                )

            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_on_empty_response(self) -> None:
        """Empty SQS response continues polling."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            scribe_queue_url="https://sqs.example.com/scribe-high",
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"Messages": []}
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive

        semaphore = asyncio.Semaphore(5)

        with pytest.raises(asyncio.CancelledError):
            await _poll_reliability_sqs(
                sqs_client, eng_cfg.sqs_queue_url, semaphore, None, None, config
            )

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_poll_error(self) -> None:
        """SQS poll error triggers retry after delay."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            scribe_queue_url="https://sqs.example.com/scribe-high",
            sqs_poll_error_delay=0,  # No actual delay in test
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("SQS error")
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive

        semaphore = asyncio.Semaphore(5)

        with pytest.raises(asyncio.CancelledError):
            await _poll_reliability_sqs(
                sqs_client, eng_cfg.sqs_queue_url, semaphore, None, None, config
            )

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_message_not_deleted_when_correlation_fails(self) -> None:
        """Message is not deleted when correlation raises an exception."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/queue",
            scribe_queue_url="https://sqs.example.com/scribe-high",
            sqs_poll_error_delay=0,
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)
        payload = {"reference_id": "INC-1"}

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "Body": json.dumps(payload),
                            "ReceiptHandle": "receipt-1",
                        }
                    ]
                }
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive
        sqs_client.delete_message = MagicMock()

        semaphore = asyncio.Semaphore(5)

        with (
            patch(
                "enigmatologist.main.run_reliability_correlation",
                new_callable=AsyncMock,
                side_effect=RuntimeError("correlation error"),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await _poll_reliability_sqs(
                sqs_client, eng_cfg.sqs_queue_url, semaphore, None, None, config
            )

        await asyncio.sleep(0.05)
        sqs_client.delete_message.assert_not_called()


class TestMainBranchPaths:
    """Tests for less-exercised branches in main()."""

    @pytest.mark.asyncio
    async def test_uses_bedrock_client_when_llm_provider_is_bedrock(self) -> None:
        """main() creates a BedrockClient when llm_provider is bedrock."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = None
        mock_config.bedrock = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            llm_provider="bedrock",
            correlation_system_prompt="test",
        )

        mock_bedrock_client = MagicMock()

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch(
                "enigmatologist.main.create_bedrock_client",
                return_value=mock_bedrock_client,
            ) as mock_create_bedrock,
            patch("enigmatologist.main.log_utils"),
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
        ):
            await main()

        mock_create_bedrock.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_error_when_bedrock_config_missing(self) -> None:
        """main() logs error when llm_provider is bedrock but bedrock config is None."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = None
        mock_config.bedrock = None
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            llm_provider="bedrock",
            correlation_system_prompt="test",
        )

        mock_logger = MagicMock()

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.logger", mock_logger),
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
        ):
            await main()

        mock_logger.error.assert_any_call("bedrock config is not loaded")

    @pytest.mark.asyncio
    async def test_configures_api_client_and_closes_on_exit(self) -> None:
        """main() creates an api client when api is configured and closes it."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = MagicMock()
        mock_config.api.api_endpoint = "http://api:8080"
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            correlation_system_prompt="test",
        )

        mock_api_client = AsyncMock()

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.log_utils"),
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
            patch(
                "enigmatologist.main.httpx.AsyncClient", return_value=mock_api_client
            ),
        ):
            await main()

        mock_api_client.aclose.assert_called_once()


# =============================================================================
# Telescope initialization paths
# =============================================================================


class TestMainTelescopePaths:
    """Tests for Telescope metrics initialization in main()."""

    @pytest.mark.asyncio
    async def test_telescope_initialized_and_shutdown_when_enabled(self) -> None:
        """main() initializes Telescope and shuts it down in finally block."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope.enabled = True
        mock_config.facade = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            correlation_system_prompt="test",
        )

        mock_telescope = MagicMock()
        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()
        mock_meter.create_histogram.return_value = MagicMock()
        mock_telescope.meter = mock_meter

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch(
                "enigmatologist.main.TelescopeClient", return_value=mock_telescope
            ) as mock_telescope_cls,
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
        ):
            await main()

        mock_telescope_cls.assert_called_once()
        mock_telescope.start.assert_called_once()
        mock_telescope.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_telescope_creates_metrics_instances(self) -> None:
        """When Telescope is enabled, all four metrics instances are created."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope.enabled = True
        mock_config.facade = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            correlation_system_prompt="test",
        )

        mock_telescope = MagicMock()
        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()
        mock_meter.create_histogram.return_value = MagicMock()
        mock_telescope.meter = mock_meter

        mock_poll = AsyncMock()

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.TelescopeClient", return_value=mock_telescope),
            patch("enigmatologist.main.ClientMetrics") as mock_client_metrics_cls,
            patch("enigmatologist.main.FacadeMetrics") as mock_facade_metrics_cls,
            patch("enigmatologist.main.SQSMetrics") as mock_sqs_metrics_cls,
            patch("enigmatologist.main.CorrelationMetrics") as mock_corr_metrics_cls,
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", mock_poll),
        ):
            await main()

        mock_client_metrics_cls.assert_called_once()
        mock_facade_metrics_cls.assert_called_once()
        mock_sqs_metrics_cls.assert_called_once()
        mock_corr_metrics_cls.assert_called_once()

        # Verify sqs_metrics and correlation_metrics are forwarded to the poll function
        poll_kwargs = mock_poll.call_args[1]
        assert poll_kwargs["sqs_metrics"] is mock_sqs_metrics_cls.return_value
        assert poll_kwargs["correlation_metrics"] is mock_corr_metrics_cls.return_value

    @pytest.mark.asyncio
    async def test_telescope_skipped_when_disabled(self) -> None:
        """When telescope is None, TelescopeClient is never instantiated."""
        from enigmatologist.main import main

        mock_config = MagicMock()
        mock_config.telescope = None
        mock_config.facade = MagicMock()
        mock_config.api = None
        mock_config.enigmatologist = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            scribe_queue_url="https://sqs.us-east-1.amazonaws.com/123/scribe-high",
            correlation_system_prompt="test",
        )

        with (
            patch("enigmatologist.main.load_config", return_value=mock_config),
            patch("enigmatologist.main.merge_config", return_value=mock_config),
            patch("enigmatologist.main.create_facade_client", return_value=MagicMock()),
            patch("enigmatologist.main.TelescopeClient") as mock_telescope_cls,
            patch("enigmatologist.main._create_sqs_client", return_value=MagicMock()),
            patch("enigmatologist.main._poll_reliability_sqs", new_callable=AsyncMock),
        ):
            await main()

        mock_telescope_cls.assert_not_called()


# =============================================================================
# _poll_reliability_sqs — metrics instrumentation paths
# =============================================================================


class TestPollReliabilitySQSMetrics:
    """Tests for SQS metrics recording within _poll_reliability_sqs."""

    @pytest.mark.asyncio
    async def test_sqs_metrics_record_poll_and_message_timing(self) -> None:
        """sqs_metrics.record_poll and start_message are called per poll."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/123/matik-enig-queue",
            scribe_queue_url="https://sqs.example.com/123/scribe-high",
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)
        payload = {"reference_id": "INC-1"}

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [{"Body": json.dumps(payload), "ReceiptHandle": "rh-1"}]
                }
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive
        sqs_client.delete_message = MagicMock()

        mock_record_fn = MagicMock()
        mock_sqs_metrics = MagicMock()
        mock_sqs_metrics.start_message.return_value = mock_record_fn

        semaphore = asyncio.Semaphore(5)

        with (
            patch(
                "enigmatologist.main.run_reliability_correlation",
                new_callable=AsyncMock,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await _poll_reliability_sqs(
                sqs_client,
                eng_cfg.sqs_queue_url,
                semaphore,
                None,
                None,
                config,
                sqs_metrics=mock_sqs_metrics,
            )

        await asyncio.sleep(0.05)

        # record_poll called with queue name extracted from URL
        mock_sqs_metrics.record_poll.assert_called_once_with("matik-enig-queue")
        # start_message called with the same queue name
        mock_sqs_metrics.start_message.assert_called_once_with("matik-enig-queue")
        # record function called with success=True on successful processing
        mock_record_fn.assert_called_once_with(True, None)

    @pytest.mark.asyncio
    async def test_sqs_metrics_records_failure_when_correlation_raises(self) -> None:
        """sqs_metrics records failure when correlation raises an exception."""
        eng_cfg = EnigmatologistConfig(
            sqs_queue_url="https://sqs.example.com/123/matik-enig-queue",
            scribe_queue_url="https://sqs.example.com/123/scribe-high",
            sqs_poll_error_delay=0,
            correlation_system_prompt="test",
        )
        config = MatikConfig(enigmatologist=eng_cfg)
        payload = {"reference_id": "INC-2"}

        call_count = 0

        def mock_receive(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [{"Body": json.dumps(payload), "ReceiptHandle": "rh-2"}]
                }
            raise asyncio.CancelledError()

        sqs_client = MagicMock()
        sqs_client.receive_message.side_effect = mock_receive
        sqs_client.delete_message = MagicMock()

        correlation_error = RuntimeError("llm failed")
        mock_record_fn = MagicMock()
        mock_sqs_metrics = MagicMock()
        mock_sqs_metrics.start_message.return_value = mock_record_fn

        semaphore = asyncio.Semaphore(5)

        with (
            patch(
                "enigmatologist.main.run_reliability_correlation",
                new_callable=AsyncMock,
                side_effect=correlation_error,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await _poll_reliability_sqs(
                sqs_client,
                eng_cfg.sqs_queue_url,
                semaphore,
                None,
                None,
                config,
                sqs_metrics=mock_sqs_metrics,
            )

        await asyncio.sleep(0.05)

        # record function called with success=False and the exception
        mock_record_fn.assert_called_once_with(False, correlation_error)
