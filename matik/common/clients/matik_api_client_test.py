"""Unit tests for matik_api_client.py."""

import itertools
import unittest
from unittest.mock import MagicMock, patch

import httpx
import pytest

from common.clients.matik_api_client import (
    DEFAULT_BACKOFF_DELAYS,
    MatikApiClient,
    _is_retryable_status_code,
    create_matik_api_client,
)
from common.models.api_config import ApiConfig
from common.utils.service_auth import SignatureVerificationError, verify_request


class TestIsRetryableStatusCode(unittest.TestCase):
    """Tests for _is_retryable_status_code function."""

    def test_500_is_retryable(self) -> None:
        """Test that 500 status code is retryable."""
        assert _is_retryable_status_code(500) is True

    def test_502_is_retryable(self) -> None:
        """Test that 502 status code is retryable."""
        assert _is_retryable_status_code(502) is True

    def test_503_is_retryable(self) -> None:
        """Test that 503 status code is retryable."""
        assert _is_retryable_status_code(503) is True

    def test_429_is_retryable(self) -> None:
        """Test that 429 rate limit status code is retryable."""
        assert _is_retryable_status_code(429) is True

    def test_400_is_not_retryable(self) -> None:
        """Test that 400 status code is not retryable."""
        assert _is_retryable_status_code(400) is False

    def test_401_is_not_retryable(self) -> None:
        """Test that 401 status code is not retryable."""
        assert _is_retryable_status_code(401) is False

    def test_404_is_not_retryable(self) -> None:
        """Test that 404 status code is not retryable."""
        assert _is_retryable_status_code(404) is False

    def test_200_is_not_retryable(self) -> None:
        """Test that 200 status code is not retryable."""
        assert _is_retryable_status_code(200) is False


class TestMatikApiClientInit(unittest.TestCase):
    """Tests for MatikApiClient initialization."""

    def test_init_sets_url(self) -> None:
        """Test that initialization sets the URL correctly."""
        config = ApiConfig(api_endpoint="https://api.example.com")
        client = MatikApiClient(api_config=config)

        assert client._url == "https://api.example.com"

    def test_init_strips_trailing_slash(self) -> None:
        """Test that trailing slash is stripped from endpoint."""
        config = ApiConfig(api_endpoint="https://api.example.com/")
        client = MatikApiClient(api_config=config)

        assert client._url == "https://api.example.com"

    def test_init_uses_default_backoff_delays(self) -> None:
        """Test that default backoff delays are used."""
        config = ApiConfig(api_endpoint="https://api.example.com")
        client = MatikApiClient(api_config=config)

        assert client.backoff_delays == DEFAULT_BACKOFF_DELAYS

    def test_init_custom_backoff_delays(self) -> None:
        """Test that custom backoff delays can be set."""
        config = ApiConfig(api_endpoint="https://api.example.com")
        custom_delays = [1.0, 2.0, 3.0]
        client = MatikApiClient(api_config=config, backoff_delays=custom_delays)

        assert client.backoff_delays == custom_delays


class TestMatikApiClientBuildUrl(unittest.TestCase):
    """Tests for MatikApiClient._build_url method."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(api_config=config)

    def test_build_url_with_leading_slash(self) -> None:
        """Test URL building with leading slash in path."""
        client = self._create_client()
        url = client._build_url("/api/v1/entities")

        assert url == "https://api.example.com/api/v1/entities"

    def test_build_url_without_leading_slash(self) -> None:
        """Test URL building without leading slash in path."""
        client = self._create_client()
        url = client._build_url("api/v1/entities")

        assert url == "https://api.example.com/api/v1/entities"


class TestMatikApiClientPostJsonRequest(unittest.TestCase):
    """Tests for MatikApiClient.post_json_request method."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(
            api_config=config,
            backoff_delays=[0.001, 0.002, 0.004],  # Very short for tests
        )

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_json_request_success(self, mock_client_class: MagicMock) -> None:
        """Test successful POST request."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client()
        result = client.post_json_request("/api/v1/analyze", {"text": "test"})

        assert result == b'{"result": "success"}'
        mock_client.request.assert_called_once()

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_json_request_retry_on_500(self, mock_client_class: MagicMock) -> None:
        """Test that 500 error triggers retry."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # Create mock responses
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.request = MagicMock()

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.request.side_effect = [error_response, success_response]

        client = self._create_client()
        result = client.post_json_request("/api/v1/analyze", {"text": "test"})

        assert result == b'{"result": "success"}'
        assert mock_client.request.call_count == 2

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_json_request_no_retry_on_400(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that 400 error does not trigger retry."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )
        mock_client.request.return_value = mock_response

        client = self._create_client()

        with pytest.raises(httpx.HTTPStatusError):
            client.post_json_request("/api/v1/analyze", {"text": "test"})

        # Should not retry on 4xx (except 429)
        assert mock_client.request.call_count == 1


class TestMatikApiClientPostRetryExhaustion(unittest.TestCase):
    """Tests for POST request retry exhaustion scenarios."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(
            api_config=config,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_retry_exhausted_raises_last_error(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that POST raises last error after all retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # All responses are 500 errors
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.request = MagicMock()

        mock_client.request.return_value = error_response

        client = self._create_client()

        with pytest.raises(httpx.HTTPStatusError):
            client.post_json_request("/api/v1/analyze", {"text": "test"})

        # Should have tried MAX_RETRIES + 1 times (4 total)
        assert mock_client.request.call_count == 4

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_generic_exception_retries(self, mock_client_class: MagicMock) -> None:
        """Test that generic exceptions trigger retries."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First call raises connection error, second succeeds
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.request.side_effect = [
            ConnectionError("Connection refused"),
            success_response,
        ]

        client = self._create_client()
        result = client.post_json_request("/api/v1/analyze", {"text": "test"})

        assert result == b'{"result": "success"}'
        assert mock_client.request.call_count == 2

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_generic_exception_exhausted(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that generic exceptions raise after retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # All calls raise connection error
        mock_client.request.side_effect = ConnectionError("Connection refused")

        client = self._create_client()

        with pytest.raises(ConnectionError, match="Connection refused"):
            client.post_json_request("/api/v1/analyze", {"text": "test"})

        assert mock_client.request.call_count == 4

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_success_after_multiple_retries(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test POST succeeds after multiple retry attempts."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First two fail, third succeeds
        error_response = MagicMock()
        error_response.status_code = 503
        error_response.request = MagicMock()

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.request.side_effect = [
            error_response,
            error_response,
            success_response,
        ]

        client = self._create_client()
        result = client.post_json_request("/api/v1/analyze", {"text": "test"})

        assert result == b'{"result": "success"}'
        assert mock_client.request.call_count == 3

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_with_none_body(self, mock_client_class: MagicMock) -> None:
        """Test POST request with None body."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client()
        result = client.post_json_request("/api/v1/analyze", None)

        assert result == b'{"result": "success"}'
        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["content"] == b""


class TestMatikApiClientGetRequest(unittest.TestCase):
    """Tests for MatikApiClient.get_request method."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(
            api_config=config,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_request_success(self, mock_client_class: MagicMock) -> None:
        """Test successful GET request."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"entities": []}'
        mock_client.get.return_value = mock_response

        client = self._create_client()
        result = client.get_request("/api/v1/entities")

        assert result == b'{"entities": []}'

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_request_with_params(self, mock_client_class: MagicMock) -> None:
        """Test GET request with query parameters."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"entities": []}'
        mock_client.get.return_value = mock_response

        client = self._create_client()
        result = client.get_request("/api/v1/entities", {"type": "component"})

        assert result == b'{"entities": []}'
        mock_client.get.assert_called_once()
        called_url = mock_client.get.call_args[0][0]
        assert called_url.params["type"] == "component"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_request_retry_on_429(self, mock_client_class: MagicMock) -> None:
        """Test that 429 rate limit triggers retry."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First response is rate limited
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.request = MagicMock()

        # Second response is success
        success = MagicMock()
        success.status_code = 200
        success.content = b'{"result": "ok"}'

        mock_client.get.side_effect = [rate_limited, success]

        client = self._create_client()
        result = client.get_request("/api/v1/entities")

        assert result == b'{"result": "ok"}'
        assert mock_client.get.call_count == 2


class TestMatikApiClientGetRetryScenarios(unittest.TestCase):
    """Tests for GET request retry scenarios."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(
            api_config=config,
            backoff_delays=[0.001, 0.002, 0.004],
        )

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_retry_exhausted_raises_last_error(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that GET raises last error after all retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # All responses are 502 errors
        error_response = MagicMock()
        error_response.status_code = 502
        error_response.request = MagicMock()

        mock_client.get.return_value = error_response

        client = self._create_client()

        with pytest.raises(httpx.HTTPStatusError):
            client.get_request("/api/v1/entities")

        assert mock_client.get.call_count == 4

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_generic_exception_retries(self, mock_client_class: MagicMock) -> None:
        """Test that generic exceptions trigger retries on GET."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.get.side_effect = [
            TimeoutError("Request timed out"),
            success_response,
        ]

        client = self._create_client()
        result = client.get_request("/api/v1/entities")

        assert result == b'{"result": "success"}'
        assert mock_client.get.call_count == 2

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_generic_exception_exhausted(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that generic exceptions raise after GET retries exhausted."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.get.side_effect = TimeoutError("Request timed out")

        client = self._create_client()

        with pytest.raises(TimeoutError, match="Request timed out"):
            client.get_request("/api/v1/entities")

        assert mock_client.get.call_count == 4

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_no_retry_on_404(self, mock_client_class: MagicMock) -> None:
        """Test that 404 error does not trigger retry."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )
        mock_client.get.return_value = mock_response

        client = self._create_client()

        with pytest.raises(httpx.HTTPStatusError):
            client.get_request("/api/v1/entities/missing")

        assert mock_client.get.call_count == 1

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_success_after_multiple_retries(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test GET succeeds after multiple retry attempts."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.request = MagicMock()

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"entities": []}'

        mock_client.get.side_effect = [
            error_response,
            error_response,
            error_response,
            success_response,
        ]

        client = self._create_client()
        result = client.get_request("/api/v1/entities")

        assert result == b'{"entities": []}'
        assert mock_client.get.call_count == 4


class TestMatikApiClientLogResponse(unittest.TestCase):
    """Tests for MatikApiClient._log_response method."""

    def _create_client(self) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(api_config=config)

    @patch("common.clients.matik_api_client.logger")
    def test_log_response_success(self, mock_logger: MagicMock) -> None:
        """Test logging for successful response."""
        client = self._create_client()
        client._log_response("https://api.example.com/test", "GET", 200)

        # Logger.info is called twice: once during __post_init__ and once in _log_response
        assert mock_logger.info.call_count == 2
        # Check that the last call (from _log_response) contains "success"
        last_call_args = mock_logger.info.call_args_list[-1][0]
        assert "success" in last_call_args[0].lower()

    @patch("common.clients.matik_api_client.logger")
    def test_log_response_error(self, mock_logger: MagicMock) -> None:
        """Test logging for error response."""
        client = self._create_client()
        client._log_response("https://api.example.com/test", "POST", 500)

        mock_logger.error.assert_called_once()
        assert "failed" in mock_logger.error.call_args[0][0].lower()


class TestMatikApiClientHeaders(unittest.TestCase):
    """Tests for MatikApiClient header support."""

    def _create_client(
        self, default_headers: dict[str, str] | None = None
    ) -> MatikApiClient:
        config = ApiConfig(api_endpoint="https://api.example.com")
        return MatikApiClient(
            api_config=config,
            backoff_delays=[0.001, 0.002, 0.004],
            default_headers=default_headers or {},
        )

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_with_default_headers(self, mock_client_class: MagicMock) -> None:
        """Test POST request includes default headers."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client(default_headers={"X-Request-ID": "test-123"})
        client.post_json_request("/api/v1/test", {"data": "test"})

        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["headers"]["Content-Type"] == "application/json"
        assert call_kwargs["headers"]["X-Request-ID"] == "test-123"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_with_request_headers(self, mock_client_class: MagicMock) -> None:
        """Test POST request with per-request headers."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client()
        client.post_json_request(
            "/api/v1/test", {"data": "test"}, headers={"X-Custom": "value"}
        )

        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["headers"]["X-Custom"] == "value"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_request_headers_override_default(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test per-request headers override default headers."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client(default_headers={"X-Request-ID": "default-id"})
        client.post_json_request(
            "/api/v1/test", {"data": "test"}, headers={"X-Request-ID": "override-id"}
        )

        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["headers"]["X-Request-ID"] == "override-id"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_with_default_headers(self, mock_client_class: MagicMock) -> None:
        """Test GET request includes default headers."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.get.return_value = mock_response

        client = self._create_client(default_headers={"X-Request-ID": "test-456"})
        client.get_request("/api/v1/test")

        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"]["X-Request-ID"] == "test-456"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_with_request_headers(self, mock_client_class: MagicMock) -> None:
        """Test GET request with per-request headers."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.get.return_value = mock_response

        client = self._create_client()
        client.get_request("/api/v1/test", headers={"X-Custom": "header-value"})

        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["headers"]["X-Custom"] == "header-value"

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_empty_headers_passes_none(self, mock_client_class: MagicMock) -> None:
        """Test GET with no headers passes None to httpx."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.get.return_value = mock_response

        client = self._create_client()  # No default headers
        client.get_request("/api/v1/test")

        call_kwargs = mock_client.get.call_args[1]
        # Empty dict or None should be passed when no headers
        assert call_kwargs["headers"] is None or call_kwargs["headers"] == {}


class TestMatikApiClientServiceSignature(unittest.TestCase):
    """Tests for Phase 1c service-signature headers on outgoing requests."""

    def _create_client(self, service_secret: str | None) -> MatikApiClient:
        config = ApiConfig(
            api_endpoint="https://api.example.com", service_secret=service_secret
        )
        return MatikApiClient(api_config=config, backoff_delays=[0.001])

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_attaches_verifiable_signature(
        self, mock_client_class: MagicMock
    ) -> None:
        """A signed POST verifies against the exact bytes actually sent."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client(service_secret="shared-secret")
        client.post_json_request("/v1/mcp/incidentio", {"data": "test"})

        call_kwargs = mock_client.request.call_args[1]
        headers = call_kwargs["headers"]
        verify_request(
            "shared-secret",
            "POST",
            "/v1/mcp/incidentio",
            call_kwargs["content"],
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_attaches_verifiable_signature(
        self, mock_client_class: MagicMock
    ) -> None:
        """A signed GET (empty body) also verifies correctly."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.get.return_value = mock_response

        client = self._create_client(service_secret="shared-secret")
        client.get_request("/v1/mcp/incidentio")

        call_kwargs = mock_client.get.call_args[1]
        headers = call_kwargs["headers"]
        verify_request(
            "shared-secret",
            "GET",
            "/v1/mcp/incidentio",
            b"",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_no_secret_configured_omits_signature_headers(
        self, mock_client_class: MagicMock
    ) -> None:
        """Without service_secret, no signature headers are sent (a no-op)."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.request.return_value = mock_response

        client = self._create_client(service_secret=None)
        client.post_json_request("/v1/mcp/incidentio", {"data": "test"})

        headers = mock_client.request.call_args[1]["headers"]
        assert "X-Matik-Service-Timestamp" not in headers
        assert "X-Matik-Service-Signature" not in headers

    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_signature_covers_query_params(
        self, mock_client_class: MagicMock
    ) -> None:
        """Query params are part of the signed material, not just the path."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"result": "success"}'
        mock_client.get.return_value = mock_response

        client = self._create_client(service_secret="shared-secret")
        client.get_request("/v1/jira/issues", params={"start_time": "2025-01-01"})

        called_url = mock_client.get.call_args[0][0]
        headers = mock_client.get.call_args[1]["headers"]
        signed_target = called_url.raw_path.decode()
        assert signed_target == "/v1/jira/issues?start_time=2025-01-01"

        verify_request(
            "shared-secret",
            "GET",
            signed_target,
            b"",
            headers["X-Matik-Service-Timestamp"],
            headers["X-Matik-Service-Signature"],
        )  # does not raise — signature covers the query string

        # Tampering the query after signing must invalidate the signature —
        # this is the gap the fix closes: previously only path+body were
        # covered, so this tamper would have gone undetected.
        with pytest.raises(SignatureVerificationError, match="signature_mismatch"):
            verify_request(
                "shared-secret",
                "GET",
                "/v1/jira/issues?start_time=2099-01-01",
                b"",
                headers["X-Matik-Service-Timestamp"],
                headers["X-Matik-Service-Signature"],
            )

    @patch("time.time")
    @patch("common.clients.matik_api_client.httpx.Client")
    def test_post_retry_recomputes_signature_with_fresh_timestamp(
        self, mock_client_class: MagicMock, mock_time: MagicMock
    ) -> None:
        """Each retry attempt signs with the then-current time, not a stale one.

        Reusing the first attempt's timestamp across retries would make a slow
        retry sequence spuriously fail signature verification once the gap
        exceeds the skew window, even though nothing about the request itself
        is invalid.
        """
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.request = MagicMock()

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.request.side_effect = [error_response, success_response]
        # An infinite, strictly-increasing counter: time.time() is also
        # called by stdlib logging for every LogRecord, so a short, finite
        # side_effect list would raise StopIteration well before the retry
        # completes. What matters here is only that the two *signature*
        # calls land on different, increasing values.
        mock_time.side_effect = itertools.count(1_700_000_000, 1)

        client = self._create_client(service_secret="shared-secret")
        client.post_json_request("/v1/mcp/incidentio", {"data": "test"})

        first_headers = mock_client.request.call_args_list[0][1]["headers"]
        second_headers = mock_client.request.call_args_list[1][1]["headers"]
        first_ts = int(first_headers["X-Matik-Service-Timestamp"])
        second_ts = int(second_headers["X-Matik-Service-Timestamp"])
        assert second_ts > first_ts
        assert (
            first_headers["X-Matik-Service-Signature"]
            != second_headers["X-Matik-Service-Signature"]
        )

    @patch("time.time")
    @patch("common.clients.matik_api_client.httpx.Client")
    def test_get_retry_recomputes_signature_with_fresh_timestamp(
        self, mock_client_class: MagicMock, mock_time: MagicMock
    ) -> None:
        """Same as the POST case above, for the GET retry path."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        error_response = MagicMock()
        error_response.status_code = 500
        error_response.request = MagicMock()

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b'{"result": "success"}'

        mock_client.get.side_effect = [error_response, success_response]
        mock_time.side_effect = itertools.count(1_700_000_000, 1)

        client = self._create_client(service_secret="shared-secret")
        client.get_request("/v1/mcp/incidentio")

        first_headers = mock_client.get.call_args_list[0][1]["headers"]
        second_headers = mock_client.get.call_args_list[1][1]["headers"]
        first_ts = int(first_headers["X-Matik-Service-Timestamp"])
        second_ts = int(second_headers["X-Matik-Service-Timestamp"])
        assert second_ts > first_ts
        assert (
            first_headers["X-Matik-Service-Signature"]
            != second_headers["X-Matik-Service-Signature"]
        )


class TestCreateMatikApiClient(unittest.TestCase):
    """Tests for create_matik_api_client factory function."""

    def test_create_client_basic(self) -> None:
        """Test factory function creates client correctly."""
        config = ApiConfig(api_endpoint="https://api.example.com")

        client = create_matik_api_client(config)

        assert isinstance(client, MatikApiClient)
        assert client._url == "https://api.example.com"
        assert client.backoff_delays == DEFAULT_BACKOFF_DELAYS
        assert client.default_headers == {}

    def test_create_client_custom_backoff(self) -> None:
        """Test factory function with custom backoff delays."""
        config = ApiConfig(api_endpoint="https://api.example.com")
        custom_delays = [5.0, 10.0, 20.0]

        client = create_matik_api_client(config, custom_delays)

        assert isinstance(client, MatikApiClient)
        assert client.backoff_delays == custom_delays

    def test_create_client_with_default_headers(self) -> None:
        """Test factory function with default headers."""
        config = ApiConfig(api_endpoint="https://api.example.com")
        headers = {"X-Request-ID": "factory-test"}

        client = create_matik_api_client(config, default_headers=headers)

        assert isinstance(client, MatikApiClient)
        assert client.default_headers == headers


if __name__ == "__main__":
    unittest.main()
