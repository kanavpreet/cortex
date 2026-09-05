"""Unit tests for facade_client.py."""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.clients.facade_client import (
    FacadeBadRequestError,
    FacadeClient,
    FacadeClientSync,
    FacadeMessage,
    create_facade_client,
    create_facade_client_sync,
    facade_assistant_message,
    facade_system_message,
    facade_user_message,
)
from common.models.common_config import CommonConfig
from common.models.facade_config import FacadeConfig


class TestFacadeMessage(unittest.TestCase):
    """Tests for FacadeMessage dataclass."""

    def test_facade_message_creation(self) -> None:
        msg = FacadeMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"


class TestMessageHelpers(unittest.TestCase):
    """Tests for message helper functions."""

    def test_facade_system_message(self) -> None:
        msg = facade_system_message("You are a helpful assistant")
        assert msg.role == "system"
        assert msg.content == "You are a helpful assistant"

    def test_facade_user_message(self) -> None:
        msg = facade_user_message("What is the capital of France?")
        assert msg.role == "user"
        assert msg.content == "What is the capital of France?"

    def test_facade_assistant_message(self) -> None:
        msg = facade_assistant_message("The capital of France is Paris.")
        assert msg.role == "assistant"
        assert msg.content == "The capital of France is Paris."


class TestFacadeClientInit(unittest.TestCase):
    """Tests for FacadeClient initialization."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[1.0, 2.0, 4.0],
        )

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_init_local_mode_with_iap_token(self, mock_openai: MagicMock) -> None:
        """Test initialization in local mode with IAP token."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        client = FacadeClient(facade_config=config, common_config=common_config)

        assert client._default_model == "gpt-4o"
        assert client._max_retries == 3
        mock_openai.assert_called_once()

    @patch.dict("os.environ", {"IAP_TOKEN": "test-iap-token"})
    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_init_local_mode_with_iap_token_env_var_set(
        self, mock_openai: MagicMock
    ) -> None:
        """Test initialization in local mode with IAP_TOKEN env var set."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        FacadeClient(facade_config=config, common_config=common_config)

        # Verify headers include Proxy-Authorization
        call_kwargs = mock_openai.call_args[1]
        assert "Proxy-Authorization" in call_kwargs["default_headers"]
        assert call_kwargs["default_headers"]["Proxy-Authorization"] == (
            "Bearer test-iap-token"
        )

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_init_kubernetes_mode(self, mock_openai: MagicMock) -> None:
        """Test initialization in Kubernetes mode (no IAP token needed)."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="production")

        client = FacadeClient(facade_config=config, common_config=common_config)

        assert client._default_model == "gpt-4o"
        mock_openai.assert_called_once()

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_init_without_common_config(self, mock_openai: MagicMock) -> None:
        """Test initialization defaults to local mode without common config."""
        config = self._create_facade_config()

        client = FacadeClient(facade_config=config)

        assert client._default_model == "gpt-4o"
        mock_openai.assert_called_once()


class TestFacadeClientSendMessage(unittest.TestCase):
    """Tests for FacadeClient.send_message method."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.01, 0.02, 0.04],
        )

    def _setup_raw_response_mock(
        self,
        mock_client: MagicMock,
        mock_completion: MagicMock,
        headers: dict[str, str] | None = None,
    ) -> AsyncMock:
        """Set up mock for with_raw_response.create pattern.

        Returns the AsyncMock for create so callers can inspect call args.
        """
        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = headers or {}

        create_mock = AsyncMock(return_value=mock_raw)
        mock_client.chat.completions.with_raw_response.create = create_mock
        return create_mock

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_success(self, mock_openai_class: MagicMock) -> None:
        """Test successful message sending."""

        async def run_test() -> str:
            # Setup mock
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Paris is the capital of France."
            mock_completion.choices = [mock_choice]

            create_mock = self._setup_raw_response_mock(mock_client, mock_completion)

            # Create client and send message
            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [
                facade_system_message("You are helpful."),
                facade_user_message("What is the capital of France?"),
            ]

            response = await client.send_message("gpt-4o", messages)
            create_mock.assert_called_once()
            return response

        response = asyncio.run(run_test())
        assert response == "Paris is the capital of France."

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_uses_default_model(
        self, mock_openai_class: MagicMock
    ) -> None:
        """Test that default model is used when model is None."""

        async def run_test() -> dict[str, str]:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Response"
            mock_completion.choices = [mock_choice]

            create_mock = self._setup_raw_response_mock(mock_client, mock_completion)

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]
            await client.send_message(None, messages)

            return dict(create_mock.call_args[1])

        call_kwargs = asyncio.run(run_test())
        assert call_kwargs["model"] == "gpt-4o"

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_empty_choices(self, mock_openai_class: MagicMock) -> None:
        """Test handling of empty choices in response."""

        async def run_test() -> str:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_completion.choices = []

            self._setup_raw_response_mock(mock_client, mock_completion)

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]
            return await client.send_message("gpt-4o", messages)

        response = asyncio.run(run_test())
        assert response == ""

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_records_rate_limits(
        self, mock_openai_class: MagicMock
    ) -> None:
        """Test that send_message records rate limit headers when facade_metrics is set."""

        async def run_test() -> MagicMock:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Response"
            mock_completion.choices = [mock_choice]

            rate_limit_headers = {
                "x-ratelimit-limit-requests": "5000",
                "x-ratelimit-remaining-requests": "4994",
            }
            self._setup_raw_response_mock(
                mock_client, mock_completion, headers=rate_limit_headers
            )

            mock_facade_metrics = MagicMock()
            mock_facade_metrics.start_call.return_value = MagicMock()

            config = self._create_facade_config()
            client = FacadeClient(
                facade_config=config, facade_metrics=mock_facade_metrics
            )

            messages = [facade_user_message("Hello")]
            await client.send_message("gpt-4o", messages, operation="test_op")

            return mock_facade_metrics

        mock_facade_metrics = asyncio.run(run_test())
        mock_facade_metrics.record_rate_limits.assert_called_once_with(
            {
                "x-ratelimit-limit-requests": "5000",
                "x-ratelimit-remaining-requests": "4994",
            },
            "gpt-4o",
            "test_op",
        )


class TestFacadeClientSendMessageWithRetry(unittest.TestCase):
    """Tests for FacadeClient.send_message_with_retry method."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],  # Very short for tests
        )

    def _setup_raw_response_mock(
        self,
        mock_client: MagicMock,
        mock_completion: MagicMock,
    ) -> AsyncMock:
        """Set up mock for with_raw_response.create pattern."""
        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = {}

        create_mock = AsyncMock(return_value=mock_raw)
        mock_client.chat.completions.with_raw_response.create = create_mock
        return create_mock

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_retry_success_after_failure(self, mock_openai_class: MagicMock) -> None:
        """Test successful retry after initial failure."""

        async def run_test() -> tuple[str, int]:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Success"
            mock_completion.choices = [mock_choice]

            mock_raw = MagicMock()
            mock_raw.parse.return_value = mock_completion
            mock_raw.headers = {}

            # First call fails, second succeeds
            create_mock = AsyncMock(side_effect=[Exception("API Error"), mock_raw])
            mock_client.chat.completions.with_raw_response.create = create_mock

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]
            response = await client.send_message_with_retry("gpt-4o", messages)

            return response, create_mock.call_count

        response, call_count = asyncio.run(run_test())
        assert response == "Success"
        assert call_count == 2

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_retry_exhausted(self, mock_openai_class: MagicMock) -> None:
        """Test that exception is raised after all retries exhausted."""

        async def run_test() -> int:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            # All calls fail
            create_mock = AsyncMock(side_effect=Exception("API Error"))
            mock_client.chat.completions.with_raw_response.create = create_mock

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]

            with pytest.raises(Exception, match="API Error"):
                await client.send_message_with_retry("gpt-4o", messages)

            return int(create_mock.call_count)

        call_count = asyncio.run(run_test())
        assert call_count == 3

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_retry_success_with_metrics(self, mock_openai_class: MagicMock) -> None:
        """Test retry success records metrics correctly."""

        async def run_test() -> tuple[str, MagicMock]:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = "Success"
            mock_completion.choices = [mock_choice]

            mock_raw = MagicMock()
            mock_raw.parse.return_value = mock_completion
            mock_raw.headers = {}

            # First call fails, second succeeds
            create_mock = AsyncMock(side_effect=[Exception("API Error"), mock_raw])
            mock_client.chat.completions.with_raw_response.create = create_mock

            # Setup mock metrics
            mock_metrics = MagicMock()

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config, metrics=mock_metrics)

            messages = [facade_user_message("Hello")]
            response = await client.send_message_with_retry("gpt-4o", messages)

            return response, mock_metrics

        response, mock_metrics = asyncio.run(run_test())
        assert response == "Success"
        # Verify record_retry was called for the first failed attempt
        mock_metrics.record_retry.assert_called_once_with("POST", "chat/completions", 1)
        # Verify record_request was called for success
        mock_metrics.record_request.assert_called_once()
        call_args = mock_metrics.record_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "chat/completions"
        assert call_args[0][3] == 200  # status code

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_retry_exhausted_with_metrics(self, mock_openai_class: MagicMock) -> None:
        """Test retry exhausted records metrics for all attempts and final failure."""

        async def run_test() -> MagicMock:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            # All calls fail
            create_mock = AsyncMock(side_effect=Exception("API Error"))
            mock_client.chat.completions.with_raw_response.create = create_mock

            # Setup mock metrics
            mock_metrics = MagicMock()

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config, metrics=mock_metrics)

            messages = [facade_user_message("Hello")]

            with pytest.raises(Exception, match="API Error"):
                await client.send_message_with_retry("gpt-4o", messages)

            return mock_metrics

        mock_metrics = asyncio.run(run_test())
        # Verify record_retry was called for each attempt
        assert mock_metrics.record_retry.call_count == 3
        # Verify record_request was called for final failure
        mock_metrics.record_request.assert_called_once()
        call_args = mock_metrics.record_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "chat/completions"
        assert call_args[0][3] == 500  # status code for failure


class TestFacadeBadRequestError(unittest.TestCase):
    """Tests for FacadeBadRequestError exception."""

    def test_error_creation(self) -> None:
        """Test creating FacadeBadRequestError with reason."""
        error = FacadeBadRequestError("Content filtered by policy")
        assert error.reason == "Content filtered by policy"
        assert str(error) == "Bad request: Content filtered by policy"

    def test_error_is_exception(self) -> None:
        """Test that FacadeBadRequestError is an Exception."""
        error = FacadeBadRequestError("Test reason")
        assert isinstance(error, Exception)

    def test_error_can_be_raised(self) -> None:
        """Test that FacadeBadRequestError can be raised and caught."""
        with pytest.raises(FacadeBadRequestError) as exc_info:
            raise FacadeBadRequestError("Test error")
        assert exc_info.value.reason == "Test error"


class TestFacadeClientBadRequestHandling(unittest.TestCase):
    """Tests for 400 Bad Request handling in FacadeClient."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_bad_request_not_retried(self, mock_openai_class: MagicMock) -> None:
        """Test that 400 errors are not retried and raise FacadeBadRequestError."""
        from openai import BadRequestError

        async def run_test() -> int:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            # Simulate BadRequestError from OpenAI SDK
            mock_error = BadRequestError(
                message="Content filtered",
                response=MagicMock(status_code=400),
                body={"error": {"message": "Content filtered"}},
            )
            create_mock = AsyncMock(side_effect=mock_error)
            mock_client.chat.completions.with_raw_response.create = create_mock

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]

            with pytest.raises(FacadeBadRequestError) as exc_info:
                await client.send_message_with_retry("gpt-4o", messages)

            assert "Content filtered" in exc_info.value.reason
            return int(create_mock.call_count)

        call_count = asyncio.run(run_test())
        # Should only be called once - no retries for 400 errors
        assert call_count == 1


class TestFacadeClientSync(unittest.TestCase):
    """Tests for FacadeClientSync class."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch.dict("os.environ", {"IAP_TOKEN": "test-iap-token"})
    def test_init_local_mode(self) -> None:
        """Test initialization in local mode with IAP_TOKEN env var."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        client = FacadeClientSync(facade_config=config, common_config=common_config)

        assert client._default_model == "gpt-4o"
        assert "Proxy-Authorization" in client._headers
        assert client._headers["Proxy-Authorization"] == "Bearer test-iap-token"

    @patch.dict("os.environ", {})
    def test_init_local_mode_without_iap_token(self) -> None:
        """Test initialization in local mode without IAP_TOKEN env var."""
        os.environ.pop("IAP_TOKEN", None)
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        client = FacadeClientSync(facade_config=config, common_config=common_config)

        assert client._default_model == "gpt-4o"
        # No Proxy-Authorization header when IAP_TOKEN not set
        assert "Proxy-Authorization" not in client._headers

    def test_init_kubernetes_mode(self) -> None:
        """Test initialization in Kubernetes mode."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="production")

        client = FacadeClientSync(facade_config=config, common_config=common_config)

        assert client._default_model == "gpt-4o"
        # No Proxy-Authorization header in production
        assert "Proxy-Authorization" not in client._headers

    @patch("common.clients.facade_client.httpx.Client")
    def test_send_message_success(self, mock_client_class: MagicMock) -> None:
        """Test successful synchronous message sending."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Paris is the capital."}}]
        }
        mock_client.post.return_value = mock_response

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("What is the capital of France?")]
        response = client.send_message("gpt-4o", messages)

        assert response == "Paris is the capital."

    @patch("common.clients.facade_client.httpx.Client")
    def test_send_message_empty_choices(self, mock_client_class: MagicMock) -> None:
        """Test handling of empty choices in response."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_client.post.return_value = mock_response

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message("gpt-4o", messages)

        assert response == ""


class TestFacadeClientSyncWrappers(unittest.TestCase):
    """Tests for FacadeClient sync wrapper methods."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            iap_token="test-iap-token",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    def _setup_raw_response_mock(
        self,
        mock_client: MagicMock,
        mock_completion: MagicMock,
    ) -> AsyncMock:
        """Set up mock for with_raw_response.create pattern."""
        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = {}

        create_mock = AsyncMock(return_value=mock_raw)
        mock_client.chat.completions.with_raw_response.create = create_mock
        return create_mock

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_sync_calls_async(self, mock_openai_class: MagicMock) -> None:
        """Test send_message_sync wrapper executes async method."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Sync response"
        mock_completion.choices = [mock_choice]

        create_mock = self._setup_raw_response_mock(mock_client, mock_completion)

        config = self._create_facade_config()
        client = FacadeClient(facade_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_sync("gpt-4o", messages)

        assert response == "Sync response"
        create_mock.assert_called_once()

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_with_retry_sync(self, mock_openai_class: MagicMock) -> None:
        """Test send_message_with_retry_sync wrapper executes async method."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Retry sync response"
        mock_completion.choices = [mock_choice]

        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = {}

        # First call fails, second succeeds
        create_mock = AsyncMock(side_effect=[Exception("API Error"), mock_raw])
        mock_client.chat.completions.with_raw_response.create = create_mock

        config = self._create_facade_config()
        client = FacadeClient(facade_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_with_retry_sync("gpt-4o", messages)

        assert response == "Retry sync response"
        assert create_mock.call_count == 2

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_none_content(self, mock_openai_class: MagicMock) -> None:
        """Test response with None content returns empty string."""

        async def run_test() -> str:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client

            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = None  # None content
            mock_completion.choices = [mock_choice]

            self._setup_raw_response_mock(mock_client, mock_completion)

            config = self._create_facade_config()
            client = FacadeClient(facade_config=config)

            messages = [facade_user_message("Hello")]
            return await client.send_message("gpt-4o", messages)

        response = asyncio.run(run_test())
        assert response == ""


class TestFacadeClientSyncRetry(unittest.TestCase):
    """Tests for FacadeClientSync.send_message_with_retry method."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            iap_token="test-iap-token",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],  # Very short for tests
        )

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_retry_success_after_failure(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client retry logic succeeds after initial failure."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First call fails, second succeeds
        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {
            "choices": [{"message": {"content": "Success after retry"}}]
        }

        mock_client.post.side_effect = [
            Exception("Connection error"),
            mock_response_success,
        ]

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_with_retry("gpt-4o", messages)

        assert response == "Success after retry"
        assert mock_client.post.call_count == 2

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_retry_exhausted(self, mock_client_class: MagicMock) -> None:
        """Test sync client raises after all retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # All calls fail
        mock_client.post.side_effect = Exception("Connection error")

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]

        with pytest.raises(Exception, match="Connection error"):
            client.send_message_with_retry("gpt-4o", messages)

        assert mock_client.post.call_count == 3

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_bad_request_not_retried(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that sync client doesn't retry 400 errors and raises FacadeBadRequestError."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # Create HTTPStatusError with status_code=400
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad request: content policy violation"

        http_error = httpx.HTTPStatusError(
            message="Client error '400 Bad Request'",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client.post.side_effect = http_error

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]

        with pytest.raises(FacadeBadRequestError) as exc_info:
            client.send_message_with_retry("gpt-4o", messages)

        assert "content policy violation" in exc_info.value.reason
        # Should only be called once - no retries for 400 errors
        assert mock_client.post.call_count == 1

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_http_status_error_retried(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that sync client retries non-400 HTTPStatusError."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # Create HTTPStatusError with status_code=500 (retriable)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        http_error = httpx.HTTPStatusError(
            message="Server error '500 Internal Server Error'",
            request=MagicMock(),
            response=mock_response,
        )

        # First call fails with 500, second succeeds
        mock_response_success = MagicMock()
        mock_response_success.json.return_value = {
            "choices": [{"message": {"content": "Success after retry"}}]
        }

        mock_client.post.side_effect = [http_error, mock_response_success]

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]
        response = client.send_message_with_retry("gpt-4o", messages)

        assert response == "Success after retry"
        assert mock_client.post.call_count == 2

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_send_message_uses_default_model(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client uses default model when None provided."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}]
        }
        mock_client.post.return_value = mock_response

        config = self._create_facade_config()
        client = FacadeClientSync(facade_config=config)

        messages = [facade_user_message("Hello")]
        client.send_message(None, messages)

        # Check the JSON payload used default model
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["model"] == "gpt-4o"


class TestFacadeClientSyncMetrics(unittest.TestCase):
    """Tests for FacadeClientSync facade_metrics integration."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_records_rate_limits(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client records rate limit headers when facade_metrics is set."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.headers = {
            "x-ratelimit-limit-requests": "5000",
            "x-ratelimit-remaining-requests": "4994",
        }
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_client.post.return_value = mock_response

        mock_facade_metrics = MagicMock()
        mock_facade_metrics.start_call.return_value = MagicMock()

        config = self._create_facade_config()
        client = FacadeClientSync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        client.send_message("gpt-4o", messages, operation="test_op")

        mock_facade_metrics.record_rate_limits.assert_called_once_with(
            mock_response.headers, "gpt-4o", "test_op"
        )

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_records_facade_metrics_on_success(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client records facade metrics on successful call."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_client.post.return_value = mock_response

        # Setup mock facade_metrics
        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock()
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_facade_config()
        client = FacadeClientSync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        response = client.send_message("gpt-4o", messages, operation="test_operation")

        assert response == "Response"
        mock_facade_metrics.start_call.assert_called_once_with(
            "gpt-4o", "test_operation"
        )
        mock_record_fn.assert_called_once_with(100, 50, False)

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_records_facade_metrics_on_error(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client records facade metrics on failed call."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = Exception("Connection error")

        # Setup mock facade_metrics
        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock()
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_facade_config()
        client = FacadeClientSync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        with pytest.raises(Exception, match="Connection error"):
            client.send_message("gpt-4o", messages, operation="test_operation")

        mock_facade_metrics.start_call.assert_called_once_with(
            "gpt-4o", "test_operation"
        )
        mock_record_fn.assert_called_once_with(0, 0, True)

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_handles_metrics_recording_error_on_success(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client handles metrics recording errors gracefully on success."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Response"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_client.post.return_value = mock_response

        # Setup mock facade_metrics that raises on record
        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock(side_effect=Exception("Metrics error"))
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_facade_config()
        client = FacadeClientSync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        # Should not raise despite metrics error
        response = client.send_message("gpt-4o", messages, operation="test_operation")

        assert response == "Response"
        mock_record_fn.assert_called_once()

    @patch("common.clients.facade_client.httpx.Client")
    def test_sync_client_handles_metrics_recording_error_on_failure(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test sync client handles metrics recording errors gracefully on failure."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = Exception("Connection error")

        # Setup mock facade_metrics that raises on record
        mock_facade_metrics = MagicMock()
        mock_record_fn = MagicMock(side_effect=Exception("Metrics error"))
        mock_facade_metrics.start_call.return_value = mock_record_fn

        config = self._create_facade_config()
        client = FacadeClientSync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        messages = [facade_user_message("Hello")]
        # Should raise the original error, not the metrics error
        with pytest.raises(Exception, match="Connection error"):
            client.send_message("gpt-4o", messages, operation="test_operation")

        mock_record_fn.assert_called_once()

    def test_sync_client_create_with_facade_metrics(self) -> None:
        """Test create_facade_client_sync passes facade_metrics to client."""
        config = self._create_facade_config()
        mock_facade_metrics = MagicMock()

        client = create_facade_client_sync(
            facade_config=config, facade_metrics=mock_facade_metrics
        )

        assert client.facade_metrics == mock_facade_metrics


class TestFactoryFunctions(unittest.TestCase):
    """Tests for factory functions."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=3,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_create_facade_client_returns_async_client(
        self, _mock_openai: MagicMock
    ) -> None:
        """Test create_facade_client returns FacadeClient instance."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        client = create_facade_client(
            facade_config=config,
            common_config=common_config,
        )

        assert isinstance(client, FacadeClient)
        assert client._default_model == "gpt-4o"

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_create_facade_client_without_common_config(
        self, _mock_openai: MagicMock
    ) -> None:
        """Test create_facade_client works without common_config."""
        config = self._create_facade_config()

        client = create_facade_client(facade_config=config)

        assert isinstance(client, FacadeClient)
        assert client._default_model == "gpt-4o"

    def test_create_facade_client_sync_returns_sync_client(self) -> None:
        """Test create_facade_client_sync returns FacadeClientSync instance."""
        config = self._create_facade_config()
        common_config = CommonConfig(environment="local")

        client = create_facade_client_sync(
            facade_config=config,
            common_config=common_config,
        )

        assert isinstance(client, FacadeClientSync)
        assert client._default_model == "gpt-4o"

    def test_create_facade_client_sync_without_common_config(self) -> None:
        """Test create_facade_client_sync works without common_config."""
        config = self._create_facade_config()

        client = create_facade_client_sync(facade_config=config)

        assert isinstance(client, FacadeClientSync)
        assert client._default_model == "gpt-4o"

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_create_facade_client_passes_config_correctly(
        self, _mock_openai: MagicMock
    ) -> None:
        """Test factory passes config to client correctly."""
        config = FacadeConfig(
            base_url="https://custom.example.com",
            resource_bucket="custom-bucket",
            default_model="custom-model",
            api_version="2024-06-01",
            max_retries=5,
            backoff_delays=[1.0, 2.0],
        )
        common_config = CommonConfig(environment="staging")

        client = create_facade_client(
            facade_config=config,
            common_config=common_config,
        )

        assert isinstance(client, FacadeClient)
        assert client._default_model == "custom-model"
        assert client._max_retries == 5
        assert client._backoff_delays == [1.0, 2.0]

    def test_create_facade_client_sync_passes_config_correctly(self) -> None:
        """Test sync factory passes config to client correctly."""
        config = FacadeConfig(
            base_url="https://custom.example.com",
            resource_bucket="custom-bucket",
            default_model="custom-model",
            api_version="2024-06-01",
            max_retries=5,
            backoff_delays=[1.0, 2.0],
        )
        common_config = CommonConfig(environment="staging")

        client = create_facade_client_sync(
            facade_config=config,
            common_config=common_config,
        )

        assert client._default_model == "custom-model"
        assert client._max_retries == 5
        assert client._backoff_delays == [1.0, 2.0]


class TestFacadeClientErrorHierarchy(unittest.TestCase):
    """Tests for FacadeClientError exception hierarchy."""

    def test_facade_client_error_is_exception(self) -> None:
        """FacadeClientError is a subclass of Exception."""
        from common.clients.facade_client import FacadeClientError

        assert issubclass(FacadeClientError, Exception)

    def test_facade_bad_request_error_inherits_facade_client_error(self) -> None:
        """FacadeBadRequestError inherits from FacadeClientError."""
        from common.clients.facade_client import FacadeClientError

        assert issubclass(FacadeBadRequestError, FacadeClientError)

    def test_facade_content_filtered_error_inherits_facade_client_error(self) -> None:
        """FacadeContentFilteredError inherits from FacadeClientError."""
        from common.clients.facade_client import (
            FacadeClientError,
            FacadeContentFilteredError,
        )

        assert issubclass(FacadeContentFilteredError, FacadeClientError)

    def test_facade_content_filtered_error_is_not_bad_request(self) -> None:
        """FacadeContentFilteredError is not a subclass of FacadeBadRequestError."""
        from common.clients.facade_client import FacadeContentFilteredError

        assert not issubclass(FacadeContentFilteredError, FacadeBadRequestError)

    def test_facade_bad_request_error_stores_reason(self) -> None:
        """FacadeBadRequestError stores the reason and includes it in str()."""
        err = FacadeBadRequestError("content policy violation")
        assert err.reason == "content policy violation"
        assert "content policy violation" in str(err)

    def test_facade_content_filtered_error_can_be_caught_as_facade_client_error(
        self,
    ) -> None:
        """FacadeContentFilteredError can be caught with except FacadeClientError."""
        from common.clients.facade_client import (
            FacadeClientError,
            FacadeContentFilteredError,
        )

        with self.assertRaises(FacadeClientError):
            raise FacadeContentFilteredError("blocked")

    def test_facade_bad_request_error_can_be_caught_as_facade_client_error(
        self,
    ) -> None:
        """FacadeBadRequestError can be caught with except FacadeClientError."""
        from common.clients.facade_client import FacadeClientError

        with self.assertRaises(FacadeClientError):
            raise FacadeBadRequestError("bad input")


class TestFacadeContentFilterDetection(unittest.TestCase):
    """Tests for content_filter finish_reason detection in send_message."""

    def _create_facade_config(self) -> FacadeConfig:
        return FacadeConfig(
            base_url="https://facade.example.com",
            resource_bucket="test-bucket",
            default_model="gpt-4o",
            api_version="2024-01-01",
            max_retries=1,
            backoff_delays=[0.01],
        )

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_raises_content_filtered_error_on_content_filter(
        self, mock_openai: MagicMock
    ) -> None:
        """send_message raises FacadeContentFilteredError when finish_reason is content_filter."""
        from common.clients.facade_client import FacadeContentFilteredError

        config = self._create_facade_config()
        mock_client_instance = MagicMock()
        mock_openai.return_value = mock_client_instance

        # Build a completion with finish_reason="content_filter"
        mock_choice = MagicMock()
        mock_choice.finish_reason = "content_filter"
        mock_choice.message.content = None

        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = None

        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = {}
        mock_client_instance.chat.completions.with_raw_response.create = AsyncMock(
            return_value=mock_raw
        )

        client = FacadeClient(facade_config=config)

        with self.assertRaises(FacadeContentFilteredError):
            asyncio.run(client.send_message(None, [facade_user_message("test")]))

    @patch("common.clients.facade_client.AsyncOpenAI")
    def test_send_message_normal_finish_reason_returns_content(
        self, mock_openai: MagicMock
    ) -> None:
        """send_message with finish_reason='stop' returns response content normally."""
        config = self._create_facade_config()
        mock_client_instance = MagicMock()
        mock_openai.return_value = mock_client_instance

        mock_choice = MagicMock()
        mock_choice.finish_reason = "stop"
        mock_choice.message.content = "The answer is 42."

        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        mock_raw = MagicMock()
        mock_raw.parse.return_value = mock_completion
        mock_raw.headers = {}
        mock_client_instance.chat.completions.with_raw_response.create = AsyncMock(
            return_value=mock_raw
        )

        client = FacadeClient(facade_config=config)

        result = asyncio.run(client.send_message(None, [facade_user_message("test")]))
        assert result == "The answer is 42."


if __name__ == "__main__":
    unittest.main()
