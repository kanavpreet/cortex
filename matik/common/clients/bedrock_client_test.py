"""Unit tests for bedrock_client.py."""

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.clients.bedrock_client import (
    BedrockClient,
    BedrockClientSync,
    create_bedrock_client,
    create_bedrock_client_sync,
)
from common.clients.facade_client import (
    FacadeBadRequestError,
    facade_system_message,
    facade_user_message,
)
from common.models.bedrock_config import BedrockConfig
from common.models.common_config import CommonConfig


def _make_bedrock_response(text: str) -> dict[str, Any]:
    """Build a minimal Bedrock Converse API response body."""
    return {
        "output": {
            "message": {
                "content": [{"text": text}],
            }
        },
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }


class TestBedrockClientInit(unittest.TestCase):
    """Tests for BedrockClient initialization."""

    def _create_bedrock_config(self) -> BedrockConfig:
        return BedrockConfig(
            base_url="https://bedrock.example.com",
            region="us-west-2",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            max_tokens=4096,
            max_retries=3,
            backoff_delays=[1.0, 2.0, 4.0],
        )

    def test_init_local_mode_sets_default_model(self) -> None:
        """Test that default model is set from config."""
        config = self._create_bedrock_config()
        client = BedrockClient(bedrock_config=config)
        assert client._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert client._max_retries == 3
        assert client._max_tokens == 4096

    @patch.dict("os.environ", {"IAP_TOKEN": "test-iap-token"})
    def test_init_local_mode_with_iap_token(self) -> None:
        """Test initialization in local mode with IAP token sets Proxy-Authorization."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="local")

        client = BedrockClient(bedrock_config=config, common_config=common_config)

        assert "Proxy-Authorization" in client._headers
        assert client._headers["Proxy-Authorization"] == "Bearer test-iap-token"
        assert client._headers["x-llm-aws-region"] == "us-west-2"

    @patch.dict("os.environ", {})
    def test_init_local_mode_without_iap_token(self) -> None:
        """Test initialization in local mode without IAP_TOKEN env var."""
        os.environ.pop("IAP_TOKEN", None)
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="local")

        client = BedrockClient(bedrock_config=config, common_config=common_config)

        assert "Proxy-Authorization" not in client._headers
        assert client._headers["x-llm-aws-region"] == "us-west-2"

    def test_init_kubernetes_mode(self) -> None:
        """Test initialization in Kubernetes mode (no IAP token needed)."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="production")

        client = BedrockClient(bedrock_config=config, common_config=common_config)

        assert "Proxy-Authorization" not in client._headers
        assert client._headers["x-llm-aws-region"] == "us-west-2"

    def test_init_without_common_config(self) -> None:
        """Test initialization defaults to local mode without common config."""
        config = self._create_bedrock_config()

        client = BedrockClient(bedrock_config=config)

        assert client._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"


class TestBedrockClientSendMessage(unittest.TestCase):
    """Tests for BedrockClient.send_message method."""

    def _create_bedrock_config(self) -> BedrockConfig:
        return BedrockConfig(
            base_url="https://bedrock.example.com",
            region="us-west-2",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            max_tokens=4096,
            max_retries=3,
            backoff_delays=[0.01, 0.02, 0.04],
        )

    def _setup_async_httpx_mock(
        self, mock_async_client_class: MagicMock, response_body: dict[str, Any]
    ) -> MagicMock:
        """Set up mock for httpx.AsyncClient context manager."""
        mock_response = MagicMock()
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_async_client_class.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_async_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_client

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_success(self, mock_async_client_class: MagicMock) -> None:
        """Test successful message sending returns response text."""

        async def run_test() -> str:
            self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Paris is the capital of France."),
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [
                facade_system_message("You are helpful."),
                facade_user_message("What is the capital of France?"),
            ]
            return await client.send_message(None, messages)

        response = asyncio.run(run_test())
        assert response == "Paris is the capital of France."

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_uses_default_model_in_url(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that the default model is used in the URL when model is None."""

        async def run_test() -> str:
            mock_client = self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Response"),
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            await client.send_message(None, messages)

            call_args = mock_client.post.call_args
            return str(call_args[0][0])  # URL positional arg

        url = asyncio.run(run_test())
        assert "us.anthropic.claude-sonnet-4-20250514-v1:0" in url
        assert url.endswith("/converse")

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_uses_explicit_model(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that an explicitly provided model overrides the default."""

        async def run_test() -> str:
            mock_client = self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Response"),
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            await client.send_message("us.meta.llama3-8b-instruct-v1:0", messages)

            call_args = mock_client.post.call_args
            return str(call_args[0][0])

        url = asyncio.run(run_test())
        assert "us.meta.llama3-8b-instruct-v1:0" in url

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_system_messages_extracted(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that system messages are extracted into 'system' field."""

        async def run_test() -> dict[str, Any]:
            mock_client = self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Response"),
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [
                facade_system_message("You are an analyst."),
                facade_user_message("Summarize this."),
            ]
            await client.send_message(None, messages)

            call_kwargs = mock_client.post.call_args[1]
            return dict(call_kwargs["json"])

        body = asyncio.run(run_test())
        assert body["system"] == [{"text": "You are an analyst."}]
        assert body["messages"] == [
            {"role": "user", "content": [{"text": "Summarize this."}]}
        ]

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_no_system_message(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test request body when no system messages are present."""

        async def run_test() -> dict[str, Any]:
            mock_client = self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Response"),
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            await client.send_message(None, messages)

            call_kwargs = mock_client.post.call_args[1]
            return dict(call_kwargs["json"])

        body = asyncio.run(run_test())
        assert "system" not in body
        assert body["messages"] == [{"role": "user", "content": [{"text": "Hello"}]}]

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_empty_response(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test handling of empty content list in Bedrock response."""

        async def run_test() -> str:
            self._setup_async_httpx_mock(
                mock_async_client_class,
                {"output": {"message": {"content": []}}, "usage": {}},
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            return await client.send_message(None, messages)

        response = asyncio.run(run_test())
        assert response == ""

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_records_facade_metrics(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that facade_metrics are recorded on successful call."""

        async def run_test() -> MagicMock:
            self._setup_async_httpx_mock(
                mock_async_client_class,
                _make_bedrock_response("Response"),
            )

            mock_facade_metrics = MagicMock()
            mock_record_fn = MagicMock()
            mock_facade_metrics.start_call.return_value = mock_record_fn

            config = self._create_bedrock_config()
            client = BedrockClient(
                bedrock_config=config, facade_metrics=mock_facade_metrics
            )

            messages = [facade_user_message("Hello")]
            await client.send_message(None, messages, operation="test_op")

            return mock_record_fn

        mock_record_fn = asyncio.run(run_test())
        mock_record_fn.assert_called_once_with(10, 5, False)

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_handles_malformed_response(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that a malformed Bedrock response (missing 'output') returns empty string."""

        async def run_test() -> str:
            self._setup_async_httpx_mock(
                mock_async_client_class, {"unexpected": "structure"}
            )
            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)
            messages = [facade_user_message("Hello")]
            return await client.send_message(None, messages)

        response = asyncio.run(run_test())
        assert response == ""

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_metrics_recording_error_on_success_swallowed(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that an exception in metrics recording on success is swallowed."""

        async def run_test() -> str:
            self._setup_async_httpx_mock(
                mock_async_client_class, _make_bedrock_response("Result")
            )
            mock_record_fn = MagicMock(side_effect=RuntimeError("metrics error"))
            mock_facade_metrics = MagicMock()
            mock_facade_metrics.start_call.return_value = mock_record_fn
            config = self._create_bedrock_config()
            client = BedrockClient(
                bedrock_config=config, facade_metrics=mock_facade_metrics
            )
            messages = [facade_user_message("Hello")]
            return await client.send_message(None, messages)

        assert asyncio.run(run_test()) == "Result"

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_metrics_recording_error_on_failure_swallowed(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that an exception in metrics recording on failure is swallowed."""

        async def run_test() -> None:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(
                side_effect=Exception("HTTP error")
            )
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )
            mock_record_fn = MagicMock(side_effect=RuntimeError("metrics error"))
            mock_facade_metrics = MagicMock()
            mock_facade_metrics.start_call.return_value = mock_record_fn
            config = self._create_bedrock_config()
            client = BedrockClient(
                bedrock_config=config, facade_metrics=mock_facade_metrics
            )
            messages = [facade_user_message("Hello")]
            with pytest.raises(Exception, match="HTTP error"):
                await client.send_message(None, messages)

        asyncio.run(run_test())

    def test_send_message_sync_returns_response(self) -> None:
        """Test that send_message_sync calls send_message and returns its result."""
        with patch("common.clients.bedrock_client.httpx.AsyncClient") as mock_cls:
            self._setup_async_httpx_mock(mock_cls, _make_bedrock_response("Sync ok"))
            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)
            response = client.send_message_sync(None, [facade_user_message("Hi")])
        assert response == "Sync ok"

    def test_send_message_with_retry_sync_returns_response(self) -> None:
        """Test that send_message_with_retry_sync calls send_message_with_retry."""
        with patch("common.clients.bedrock_client.httpx.AsyncClient") as mock_cls:
            self._setup_async_httpx_mock(
                mock_cls, _make_bedrock_response("Retry sync ok")
            )
            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)
            response = client.send_message_with_retry_sync(
                None, [facade_user_message("Hi")]
            )
        assert response == "Retry sync ok"

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_send_message_records_facade_metrics_on_error(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test that facade_metrics error flag is set on failed call."""

        async def run_test() -> MagicMock:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(
                side_effect=Exception("Connection error")
            )

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            mock_facade_metrics = MagicMock()
            mock_record_fn = MagicMock()
            mock_facade_metrics.start_call.return_value = mock_record_fn

            config = self._create_bedrock_config()
            client = BedrockClient(
                bedrock_config=config, facade_metrics=mock_facade_metrics
            )

            messages = [facade_user_message("Hello")]
            with pytest.raises(Exception, match="Connection error"):
                await client.send_message(None, messages)

            return mock_record_fn

        mock_record_fn = asyncio.run(run_test())
        mock_record_fn.assert_called_once_with(0, 0, True)


class TestBedrockClientSendMessageWithRetry(unittest.TestCase):
    """Tests for BedrockClient.send_message_with_retry method."""

    def _create_bedrock_config(self) -> BedrockConfig:
        return BedrockConfig(
            base_url="https://bedrock.example.com",
            region="us-west-2",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            max_tokens=4096,
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    def _setup_async_httpx_mock(
        self, mock_async_client_class: MagicMock, response_body: dict[str, Any]
    ) -> MagicMock:
        """Set up mock for httpx.AsyncClient context manager for success."""
        mock_response = MagicMock()
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_async_client_class.return_value.__aenter__ = AsyncMock(
            return_value=mock_client
        )
        mock_async_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        return mock_client

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_retry_success_after_failure(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Test successful retry after initial failure."""

        async def run_test() -> tuple[str, int]:
            # Mock: first call raises, second succeeds
            mock_response_ok = MagicMock()
            mock_response_ok.json.return_value = _make_bedrock_response(
                "Success after retry"
            )
            mock_response_ok.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(
                side_effect=[Exception("Transient error"), mock_response_ok]
            )

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            response = await client.send_message_with_retry(None, messages)

            return response, mock_client.post.call_count

        response, call_count = asyncio.run(run_test())
        assert response == "Success after retry"
        assert call_count == 2

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_retry_exhausted(self, mock_async_client_class: MagicMock) -> None:
        """Test that exception is raised after all retries exhausted."""

        async def run_test() -> int:
            mock_client = MagicMock()
            mock_client.post = AsyncMock(side_effect=Exception("Persistent error"))

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            with pytest.raises(Exception, match="Persistent error"):
                await client.send_message_with_retry(None, messages)

            return int(mock_client.post.call_count)

        call_count = asyncio.run(run_test())
        assert call_count == 3

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_bad_request_not_retried(self, mock_async_client_class: MagicMock) -> None:
        """Test that 400 errors are not retried and raise FacadeBadRequestError."""

        async def run_test() -> int:
            mock_http_response = MagicMock()
            mock_http_response.status_code = 400
            mock_http_response.text = "Content policy violation"

            http_error = httpx.HTTPStatusError(
                message="Client error '400 Bad Request'",
                request=MagicMock(),
                response=mock_http_response,
            )

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=http_error)

            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config)

            messages = [facade_user_message("Hello")]
            with pytest.raises(FacadeBadRequestError) as exc_info:
                await client.send_message_with_retry(None, messages)

            assert "Content policy violation" in exc_info.value.reason
            return int(mock_client.post.call_count)

        call_count = asyncio.run(run_test())
        assert call_count == 1

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_retry_records_metrics(self, mock_async_client_class: MagicMock) -> None:
        """Test that retry records metrics correctly."""

        async def run_test() -> tuple[str, MagicMock]:
            mock_response_ok = MagicMock()
            mock_response_ok.json.return_value = _make_bedrock_response("Success")
            mock_response_ok.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(
                side_effect=[Exception("Error"), mock_response_ok]
            )

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            mock_metrics = MagicMock()

            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config, metrics=mock_metrics)

            messages = [facade_user_message("Hello")]
            response = await client.send_message_with_retry(None, messages)

            return response, mock_metrics

        response, mock_metrics = asyncio.run(run_test())
        assert response == "Success"
        mock_metrics.record_retry.assert_called_once()
        mock_metrics.record_request.assert_called_once()
        call_args = mock_metrics.record_request.call_args
        assert call_args[0][3] == 200

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_http_status_error_non_400_with_metrics(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """Non-400 HTTPStatusError triggers metrics record_retry on the HTTP error path."""

        async def run_test() -> tuple[str, MagicMock]:
            mock_http_response = MagicMock()
            mock_http_response.status_code = 503
            mock_http_response.text = "Service unavailable"

            http_error = httpx.HTTPStatusError(
                message="Server error '503'",
                request=MagicMock(),
                response=mock_http_response,
            )

            mock_response_ok = MagicMock()
            mock_response_ok.json.return_value = _make_bedrock_response("Success")
            mock_response_ok.raise_for_status = MagicMock()

            mock_client = MagicMock()
            mock_client.post = AsyncMock(side_effect=[http_error, mock_response_ok])

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            mock_metrics = MagicMock()
            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config, metrics=mock_metrics)

            messages = [facade_user_message("Hello")]
            response = await client.send_message_with_retry(None, messages)
            return response, mock_metrics

        response, mock_metrics = asyncio.run(run_test())
        assert response == "Success"
        mock_metrics.record_retry.assert_called_once()

    @patch("common.clients.bedrock_client.httpx.AsyncClient")
    def test_retry_exhausted_records_failure_metrics(
        self, mock_async_client_class: MagicMock
    ) -> None:
        """After all retries exhausted, metrics records a 500 failure."""

        async def run_test() -> MagicMock:
            mock_client = MagicMock()
            mock_client.post = AsyncMock(side_effect=Exception("persistent error"))

            mock_async_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_async_client_class.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            mock_metrics = MagicMock()
            config = self._create_bedrock_config()
            client = BedrockClient(bedrock_config=config, metrics=mock_metrics)

            messages = [facade_user_message("Hello")]
            with pytest.raises(Exception, match="persistent error"):
                await client.send_message_with_retry(None, messages)

            return mock_metrics

        mock_metrics = asyncio.run(run_test())
        call_args = mock_metrics.record_request.call_args
        assert call_args[0][3] == 500


class TestBedrockClientSync(unittest.TestCase):
    """Tests for BedrockClientSync class."""

    def _create_bedrock_config(self) -> BedrockConfig:
        return BedrockConfig(
            base_url="https://bedrock.example.com",
            region="us-west-2",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            max_tokens=4096,
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch.dict("os.environ", {"IAP_TOKEN": "test-iap-token"})
    def test_init_local_mode_with_iap_token(self) -> None:
        """Test initialization in local mode with IAP_TOKEN env var."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="local")

        client = BedrockClientSync(bedrock_config=config, common_config=common_config)

        assert client._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert "Proxy-Authorization" in client._headers
        assert client._headers["Proxy-Authorization"] == "Bearer test-iap-token"
        assert client._headers["x-llm-aws-region"] == "us-west-2"

    @patch.dict("os.environ", {})
    def test_init_local_mode_without_iap_token(self) -> None:
        """Test initialization in local mode without IAP_TOKEN."""
        import os

        os.environ.pop("IAP_TOKEN", None)
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="local")

        client = BedrockClientSync(bedrock_config=config, common_config=common_config)

        assert "Proxy-Authorization" not in client._headers

    def test_init_kubernetes_mode(self) -> None:
        """Test initialization in Kubernetes mode."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="production")

        client = BedrockClientSync(bedrock_config=config, common_config=common_config)

        assert "Proxy-Authorization" not in client._headers

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_send_message_success(self, mock_client_class: MagicMock) -> None:
        """Test successful synchronous message sending."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = _make_bedrock_response(
            "Paris is the capital."
        )
        mock_client.post.return_value = mock_response

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("What is the capital of France?")]
        response = client.send_message(None, messages)

        assert response == "Paris is the capital."

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_send_message_uses_default_model_in_url(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client uses default model in URL."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = _make_bedrock_response("Response")
        mock_client.post.return_value = mock_response

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("Hello")]
        client.send_message(None, messages)

        call_args = mock_client.post.call_args
        url = call_args[0][0]
        assert "us.anthropic.claude-sonnet-4-20250514-v1:0" in url
        assert url.endswith("/converse")

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_send_message_system_messages_extracted(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client extracts system messages into 'system' field."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = _make_bedrock_response("Response")
        mock_client.post.return_value = mock_response

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [
            facade_system_message("You are an analyst."),
            facade_user_message("Summarize this."),
        ]
        client.send_message(None, messages)

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["system"] == [{"text": "You are an analyst."}]
        assert body["messages"] == [
            {"role": "user", "content": [{"text": "Summarize this."}]}
        ]

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_retry_success_after_failure(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync retry logic succeeds after initial failure."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response_ok = MagicMock()
        mock_response_ok.json.return_value = _make_bedrock_response(
            "Success after retry"
        )
        mock_client.post.side_effect = [
            Exception("Connection error"),
            mock_response_ok,
        ]

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_with_retry(None, messages)

        assert response == "Success after retry"
        assert mock_client.post.call_count == 2

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_retry_exhausted(self, mock_client_class: MagicMock) -> None:
        """Test sync client raises after all retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = Exception("Connection error")

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("Hello")]
        with pytest.raises(Exception, match="Connection error"):
            client.send_message_with_retry(None, messages)

        assert mock_client.post.call_count == 3

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_bad_request_not_retried(self, mock_client_class: MagicMock) -> None:
        """Test sync client doesn't retry 400 errors and raises FacadeBadRequestError."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 400
        mock_http_response.text = "Content policy violation"

        http_error = httpx.HTTPStatusError(
            message="Client error '400 Bad Request'",
            request=MagicMock(),
            response=mock_http_response,
        )
        mock_client.post.side_effect = http_error

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("Hello")]
        with pytest.raises(FacadeBadRequestError) as exc_info:
            client.send_message_with_retry(None, messages)

        assert "Content policy violation" in exc_info.value.reason
        assert mock_client.post.call_count == 1

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_non_400_http_error_is_retried(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that non-400 HTTP errors are retried."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_http_response = MagicMock()
        mock_http_response.status_code = 500
        mock_http_response.text = "Internal server error"

        http_error = httpx.HTTPStatusError(
            message="Server error '500'",
            request=MagicMock(),
            response=mock_http_response,
        )

        mock_response_ok = MagicMock()
        mock_response_ok.json.return_value = _make_bedrock_response("Success")
        mock_client.post.side_effect = [http_error, mock_response_ok]

        config = self._create_bedrock_config()
        client = BedrockClientSync(bedrock_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_with_retry(None, messages)

        assert response == "Success"
        assert mock_client.post.call_count == 2

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_records_facade_metrics_on_success(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client records facade metrics on successful call."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = _make_bedrock_response("Response")
        mock_client.post.return_value = mock_response

        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock()
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_bedrock_config()
        client = BedrockClientSync(
            bedrock_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        response = client.send_message(None, messages, operation="test_op")

        assert response == "Response"
        mock_facade_metrics.start_call.assert_called_once_with(
            "us.anthropic.claude-sonnet-4-20250514-v1:0", "test_op"
        )
        mock_record_fn.assert_called_once_with(10, 5, False)

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_records_facade_metrics_on_error(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client records facade metrics error on failed call."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = Exception("Connection error")

        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock()
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_bedrock_config()
        client = BedrockClientSync(
            bedrock_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        with pytest.raises(Exception, match="Connection error"):
            client.send_message(None, messages)

        mock_record_fn.assert_called_once_with(0, 0, True)

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_facade_metrics_recording_error_on_success_swallowed(
        self, mock_client_class: MagicMock
    ) -> None:
        """Sync client swallows exception when recording facade metrics on success."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = _make_bedrock_response("Response")
        mock_client.post.return_value = mock_response

        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock(side_effect=Exception("metrics failure"))
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_bedrock_config()
        client = BedrockClientSync(
            bedrock_config=config, facade_metrics=mock_facade_metrics
        )

        response = client.send_message(None, [facade_user_message("Hello")])
        assert response == "Response"

    @patch("common.clients.bedrock_client.httpx.Client")
    def test_sync_facade_metrics_recording_error_on_failure_swallowed(
        self, mock_client_class: MagicMock
    ) -> None:
        """Sync client swallows exception when recording facade metrics on failure."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = Exception("Connection error")

        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock(side_effect=Exception("metrics failure"))
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_bedrock_config()
        client = BedrockClientSync(
            bedrock_config=config, facade_metrics=mock_facade_metrics
        )

        with pytest.raises(Exception, match="Connection error"):
            client.send_message(None, [facade_user_message("Hello")])


class TestFactoryFunctions(unittest.TestCase):
    """Tests for factory functions."""

    def _create_bedrock_config(self) -> BedrockConfig:
        return BedrockConfig(
            base_url="https://bedrock.example.com",
            region="us-west-2",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            max_tokens=4096,
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    def test_create_bedrock_client_returns_async_client(self) -> None:
        """Test create_bedrock_client returns BedrockClient instance."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="production")

        client = create_bedrock_client(
            bedrock_config=config, common_config=common_config
        )

        assert isinstance(client, BedrockClient)
        assert client._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def test_create_bedrock_client_without_common_config(self) -> None:
        """Test create_bedrock_client works without common_config."""
        config = self._create_bedrock_config()

        client = create_bedrock_client(bedrock_config=config)

        assert isinstance(client, BedrockClient)
        assert client._max_retries == 3

    def test_create_bedrock_client_sync_returns_sync_client(self) -> None:
        """Test create_bedrock_client_sync returns BedrockClientSync instance."""
        config = self._create_bedrock_config()
        common_config = CommonConfig(environment="production")

        client = create_bedrock_client_sync(
            bedrock_config=config, common_config=common_config
        )

        assert isinstance(client, BedrockClientSync)
        assert client._default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def test_create_bedrock_client_sync_without_common_config(self) -> None:
        """Test create_bedrock_client_sync works without common_config."""
        config = self._create_bedrock_config()

        client = create_bedrock_client_sync(bedrock_config=config)

        assert isinstance(client, BedrockClientSync)
        assert client._max_retries == 3

    def test_create_bedrock_client_passes_config_correctly(self) -> None:
        """Test factory passes config values to client correctly."""
        config = BedrockConfig(
            base_url="https://custom.bedrock.example.com",
            region="eu-west-1",
            default_model="us.meta.llama3-8b-instruct-v1:0",
            max_tokens=2048,
            max_retries=5,
            backoff_delays=[1.0, 2.0],
        )

        client = create_bedrock_client(bedrock_config=config)

        assert client._default_model == "us.meta.llama3-8b-instruct-v1:0"
        assert client._max_retries == 5
        assert client._backoff_delays == [1.0, 2.0]
        assert client._max_tokens == 2048
        assert client._region == "eu-west-1"

    def test_create_bedrock_client_passes_facade_metrics(self) -> None:
        """Test create_bedrock_client_sync passes facade_metrics to client."""
        config = self._create_bedrock_config()
        mock_facade_metrics = MagicMock()

        client = create_bedrock_client_sync(
            bedrock_config=config, facade_metrics=mock_facade_metrics
        )

        assert client.facade_metrics == mock_facade_metrics


if __name__ == "__main__":
    unittest.main()
