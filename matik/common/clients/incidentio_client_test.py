"""Unit tests for incidentio_client.py."""

import asyncio
import unittest
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.clients.incidentio_client import (
    IncidentIOClient,
    IncidentIOClientSync,
    IncidentWithRawFields,
    create_incidentio_client,
    create_incidentio_client_sync,
)
from common.models.incidentio_config import IncidentIOConfig


def _create_config(
    api_key: str = "test-api-key",
    base_url: str = "https://api.incident.io",
    api_version: str = "v2",
    page_size: int = 100,
    max_page_size: int = 500,
    max_retries: int = 3,
    timeout: float = 30.0,
    backoff_delays: list[float] | None = None,
) -> IncidentIOConfig:
    """Helper to create test config with optional overrides."""
    return IncidentIOConfig(
        api_key=api_key,
        base_url=base_url,
        api_version=api_version,
        page_size=page_size,
        max_page_size=max_page_size,
        max_retries=max_retries,
        timeout=timeout,
        backoff_delays=backoff_delays or [2.0, 4.0, 8.0],
        root_cause_prompt="Test root cause prompt",
        description_prompt="Test description prompt",
    )


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Helper to run async code in tests without pytest-asyncio."""
    return asyncio.run(coro)


class TestIncidentIOClientInit(unittest.TestCase):
    """Tests for IncidentIOClient initialization."""

    def test_init_with_defaults(self) -> None:
        """Test initialization with default config values."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        assert client.incidentio_config.base_url == "https://api.incident.io"
        assert client.incidentio_config.timeout == 30.0
        assert "Authorization" in client._headers
        assert client._headers["Authorization"] == "Bearer test-api-key"

    def test_init_with_custom_url(self) -> None:
        """Test initialization with custom base URL in config."""
        config = _create_config(base_url="https://custom.incident.io")
        client = IncidentIOClient(incidentio_config=config)

        assert client.incidentio_config.base_url == "https://custom.incident.io"

    def test_init_with_custom_timeout(self) -> None:
        """Test initialization with custom timeout in config."""
        config = _create_config(timeout=60.0)
        client = IncidentIOClient(incidentio_config=config)

        assert client.incidentio_config.timeout == 60.0

    def test_init_with_custom_backoff_delays(self) -> None:
        """Test initialization with custom backoff delays in config."""
        custom_delays = [1.0, 2.0, 3.0]
        config = _create_config(backoff_delays=custom_delays)
        client = IncidentIOClient(incidentio_config=config)

        assert client.incidentio_config.backoff_delays == custom_delays


class TestIncidentIOClientSyncInit(unittest.TestCase):
    """Tests for IncidentIOClientSync initialization."""

    def test_init_sync_client(self) -> None:
        """Test sync client initialization."""
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        assert client._async_client is not None
        assert isinstance(client._async_client, IncidentIOClient)

    def test_init_sync_client_passes_config(self) -> None:
        """Test sync client passes config to async client."""
        config = _create_config(
            base_url="https://custom.incident.io",
            timeout=60.0,
        )
        client = IncidentIOClientSync(incidentio_config=config)

        assert (
            client._async_client.incidentio_config.base_url
            == "https://custom.incident.io"
        )
        assert client._async_client.incidentio_config.timeout == 60.0


class TestIncidentIOClientRetry(unittest.TestCase):
    """Tests for retry logic."""

    def test_retry_on_500(self) -> None:
        """Test retry on 500 error.

        Validation: Covers _request_with_retry method
        - Line 91: async with httpx.AsyncClient
        - Lines 93-101: retry delay logic
        - Lines 104-111: GET request execution
        - Lines 114-125: retryable status code handling
        - Lines 127-129: successful response return
        """
        config = _create_config(backoff_delays=[0.001, 0.001, 0.001])
        client = IncidentIOClient(incidentio_config=config)

        with (
            patch(
                "common.clients.incidentio_client.httpx.AsyncClient"
            ) as mock_client_class,
            patch(
                "common.clients.incidentio_client.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            error_response = MagicMock()
            error_response.status_code = 500
            error_response.request = MagicMock()

            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {"data": "success"}
            success_response.raise_for_status = MagicMock()

            mock_client.get.side_effect = [error_response, success_response]

            result = _run_async(
                client._request_with_retry("GET", "https://api.example.com/test")
            )

            assert result == {"data": "success"}
            assert mock_client.get.call_count == 2

    def test_retry_on_429(self) -> None:
        """Test retry on 429 rate limit error.

        Validation: Covers retry on rate limiting (line 114 RETRYABLE_STATUS_CODES)
        """
        config = _create_config(backoff_delays=[0.001, 0.001, 0.001])
        client = IncidentIOClient(incidentio_config=config)

        with (
            patch(
                "common.clients.incidentio_client.httpx.AsyncClient"
            ) as mock_client_class,
            patch(
                "common.clients.incidentio_client.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            rate_limit_response = MagicMock()
            rate_limit_response.status_code = 429
            rate_limit_response.request = MagicMock()

            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {"data": "success"}
            success_response.raise_for_status = MagicMock()

            mock_client.get.side_effect = [rate_limit_response, success_response]

            result = _run_async(
                client._request_with_retry("GET", "https://api.example.com/test")
            )

            assert result == {"data": "success"}
            assert mock_client.get.call_count == 2

    def test_retry_on_timeout(self) -> None:
        """Test retry on timeout exception.

        Validation: Covers lines 131-133 (TimeoutException handling)
        """
        config = _create_config(backoff_delays=[0.001, 0.001, 0.001])
        client = IncidentIOClient(incidentio_config=config)

        with (
            patch(
                "common.clients.incidentio_client.httpx.AsyncClient"
            ) as mock_client_class,
            patch(
                "common.clients.incidentio_client.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {"data": "success"}
            success_response.raise_for_status = MagicMock()

            mock_client.get.side_effect = [
                httpx.TimeoutException("timeout"),
                success_response,
            ]

            result = _run_async(
                client._request_with_retry("GET", "https://api.example.com/test")
            )

            assert result == {"data": "success"}
            assert mock_client.get.call_count == 2

    def test_retry_on_connection_error(self) -> None:
        """Test retry on connection error.

        Validation: Covers lines 134-136 (ConnectError handling)
        """
        config = _create_config(backoff_delays=[0.001, 0.001, 0.001])
        client = IncidentIOClient(incidentio_config=config)

        with (
            patch(
                "common.clients.incidentio_client.httpx.AsyncClient"
            ) as mock_client_class,
            patch(
                "common.clients.incidentio_client.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {"data": "success"}
            success_response.raise_for_status = MagicMock()

            mock_client.get.side_effect = [
                httpx.ConnectError("connection failed"),
                success_response,
            ]

            result = _run_async(
                client._request_with_retry("GET", "https://api.example.com/test")
            )

            assert result == {"data": "success"}
            assert mock_client.get.call_count == 2

    def test_no_retry_on_400(self) -> None:
        """Test no retry on 400 error (client error).

        Validation: Covers lines 137-138 (HTTPStatusError re-raise)
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            error_response = MagicMock()
            error_response.status_code = 400
            error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Bad Request",
                request=MagicMock(),
                response=error_response,
            )

            mock_client.get.return_value = error_response

            with pytest.raises(httpx.HTTPStatusError):
                _run_async(
                    client._request_with_retry("GET", "https://api.example.com/test")
                )

            assert mock_client.get.call_count == 1

    def test_max_retries_exceeded(self) -> None:
        """Test that error is raised after max retries.

        Validation: Covers lines 140-147 (error logging and re-raise after max retries)
        """
        config = _create_config(backoff_delays=[0.001, 0.001, 0.001])
        client = IncidentIOClient(incidentio_config=config)

        with (
            patch(
                "common.clients.incidentio_client.httpx.AsyncClient"
            ) as mock_client_class,
            patch(
                "common.clients.incidentio_client.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            error_response = MagicMock()
            error_response.status_code = 500
            error_response.request = MagicMock()

            mock_client.get.return_value = error_response

            with pytest.raises(httpx.HTTPStatusError):
                _run_async(
                    client._request_with_retry("GET", "https://api.example.com/test")
                )

            # Initial + 3 retries = 4 attempts
            assert mock_client.get.call_count == 4

    def test_non_get_request(self) -> None:
        """Test non-GET request method.

        Validation: Covers lines 108-111 (client.request for non-GET methods)
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            success_response = MagicMock()
            success_response.status_code = 200
            success_response.json.return_value = {"data": "success"}
            success_response.raise_for_status = MagicMock()

            mock_client.request.return_value = success_response

            result = _run_async(
                client._request_with_retry("POST", "https://api.example.com/test")
            )

            assert result == {"data": "success"}
            mock_client.request.assert_called_once()


# =============================================================================
# list_incidents tests
# =============================================================================


class TestIncidentIOClientListIncidents(unittest.TestCase):
    """Tests for list_incidents method."""

    def test_list_incidents_empty(self) -> None:
        """Test listing incidents with empty response.

        Validation: Covers list_incidents method
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents, raw_count, last_raw_id = _run_async(client.list_incidents())

            assert incidents == []
            assert raw_count == 0
            assert last_raw_id is None
            mock_client.get.assert_called_once()

    def test_list_incidents_single(self) -> None:
        """Test listing incidents with single incident.

        Validation: Covers incident processing loop
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        mock_api_response = {
            "incidents": [
                {
                    "id": "01ABC123",
                    "reference": "INC-1234",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123456",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [
                        {
                            "name": "reported",
                            "value": {"last_occurred_at": "2024-01-15T10:00:00Z"},
                        },
                        {
                            "name": "updated_at",
                            "value": {"last_occurred_at": "2024-01-15T12:00:00Z"},
                        },
                    ],
                    "custom_field_entries": [],
                }
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents, raw_count, last_raw_id = _run_async(client.list_incidents())

            assert len(incidents) == 1
            assert raw_count == 1
            assert last_raw_id == "01ABC123"
            assert isinstance(incidents[0], IncidentWithRawFields)
            assert incidents[0].incident.incident_id == "01ABC123"
            assert incidents[0].incident.reference_id == "INC-1234"

    def test_list_incidents_filters_non_public(self) -> None:
        """Test that non-public incidents are filtered out.

        Validation: Only incidents with visibility='public' are returned.
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        mock_api_response = {
            "incidents": [
                {
                    "id": "01ABC123",
                    "reference": "INC-1",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "custom_field_entries": [],
                },
                {
                    "id": "02DEF456",
                    "reference": "INC-2",
                    "severity": {"name": "Sev 2"},
                    "slack_channel_id": "C456",
                    "incident_status": {"name": "closed"},
                    "visibility": "private",  # Should be filtered out
                    "created_at": "2024-01-15T11:00:00Z",
                    "custom_field_entries": [],
                },
                {
                    "id": "03GHI789",
                    "reference": "INC-3",
                    "severity": {"name": "Sev 3"},
                    "slack_channel_id": "C789",
                    "incident_status": {"name": "open"},
                    "visibility": "public",
                    "created_at": "2024-01-15T12:00:00Z",
                    "custom_field_entries": [],
                },
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents, raw_count, last_raw_id = _run_async(client.list_incidents())

            # Only 2 public incidents should be returned
            assert len(incidents) == 2
            assert incidents[0].incident.incident_id == "01ABC123"
            assert incidents[1].incident.incident_id == "03GHI789"

            # Raw count should be 3 (all incidents before filtering)
            assert raw_count == 3
            # Last raw ID should be from the last incident in raw response
            assert last_raw_id == "03GHI789"

    def test_list_incidents_pagination_params(self) -> None:
        """Test pagination parameters are passed correctly.

        Validation: Covers params construction with after/page_size
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(client.list_incidents(after="last-id", page_size=50))

            # Verify params passed to httpx
            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["after"] == "last-id"
            assert params["page_size"] == 50

    def test_list_incidents_created_at_gte_param(self) -> None:
        """Test created_at_gte is passed as created_at[gte] query param."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(client.list_incidents(created_at_gte="2024-01-01"))

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["created_at[gte]"] == "2024-01-01"
            assert "updated_at[gte]" not in params

    def test_list_incidents_both_date_filters(self) -> None:
        """Test both created_at_gte and updated_at_gte can be passed."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(
                client.list_incidents(
                    updated_at_gte="2024-01-01",
                    created_at_gte="2024-02-01",
                )
            )

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["updated_at[gte]"] == "2024-01-01"
            assert params["created_at[gte]"] == "2024-02-01"

    def test_list_incidents_incident_type_ids_param(self) -> None:
        """Test incident_type_ids is passed as incident_type[one_of] query param."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(
                client.list_incidents(incident_type_ids=["01HDCV644PMEQQTNKB4MNKTMSM"])
            )

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["incident_type[one_of]"] == ["01HDCV644PMEQQTNKB4MNKTMSM"]
            assert "created_at[gte]" not in params

    def test_list_incidents_clamps_page_size(self) -> None:
        """Test page_size is clamped to max value.

        Validation: Covers page_size = min(page_size, MAX_PAGE_SIZE)
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(client.list_incidents(page_size=1000))

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["page_size"] == 500  # Clamped to MAX_PAGE_SIZE


# =============================================================================
# get_incident tests
# =============================================================================


class TestIncidentIOClientGetIncident(unittest.TestCase):
    """Tests for get_incident method."""

    def test_get_incident_returns_raw_payload(self) -> None:
        """Test the raw API response is returned untouched, not parsed into
        IncidentWithRawFields."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        raw_payload = {
            "incident": {
                "id": "01ABC123",
                "reference": "INC-1234",
                "postmortem": {"content": "Root cause was PR #42"},
            }
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = raw_payload
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            result = _run_async(client.get_incident("01ABC123"))

            assert result == raw_payload

    def test_get_incident_url_uses_internal_id(self) -> None:
        """Test the request targets /incidents/{incident_id}, not the reference."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(client.get_incident("01ABC123"))

            call_args = mock_client.get.call_args
            url = call_args.kwargs.get("url", call_args[0][0] if call_args[0] else None)
            assert url is not None
            assert url.endswith("/v2/incidents/01ABC123")


# =============================================================================
# list_incident_updates tests
# =============================================================================


class TestIncidentIOClientListIncidentUpdates(unittest.TestCase):
    """Tests for list_incident_updates method."""

    def test_returns_updates_oldest_first(self) -> None:
        """Test the API's newest-first order is reversed to chronological."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "incident_updates": [
                    {
                        "id": "u2",
                        "created_at": "2024-01-02T00:00:00Z",
                        "message": "newer",
                    },
                    {
                        "id": "u1",
                        "created_at": "2024-01-01T00:00:00Z",
                        "message": "older",
                    },
                ],
                "pagination_meta": {"page_size": 25, "after": None},
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            updates = _run_async(client.list_incident_updates("01ABC123"))

            assert [u["id"] for u in updates] == ["u1", "u2"]

    def test_url_and_incident_id_param(self) -> None:
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "incident_updates": [],
                "pagination_meta": {"page_size": 25, "after": None},
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(client.list_incident_updates("01ABC123"))

            call_args = mock_client.get.call_args
            url = call_args.kwargs.get("url", call_args[0][0] if call_args[0] else None)
            assert url is not None
            assert url.endswith("/v2/incident_updates")
            params = call_args.kwargs.get(
                "params", call_args[0][1] if len(call_args[0]) > 1 else None
            )
            assert params["incident_id"] == "01ABC123"

    def test_paginates_across_multiple_pages(self) -> None:
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            first_page = MagicMock()
            first_page.status_code = 200
            first_page.json.return_value = {
                "incident_updates": [
                    {"id": "u2", "created_at": "2024-01-02T00:00:00Z"}
                ],
                "pagination_meta": {"page_size": 1, "after": "cursor-1"},
            }
            first_page.raise_for_status = MagicMock()

            second_page = MagicMock()
            second_page.status_code = 200
            second_page.json.return_value = {
                "incident_updates": [
                    {"id": "u1", "created_at": "2024-01-01T00:00:00Z"}
                ],
                "pagination_meta": {"page_size": 1, "after": None},
            }
            second_page.raise_for_status = MagicMock()

            mock_client.get.side_effect = [first_page, second_page]

            updates = _run_async(client.list_incident_updates("01ABC123"))

            assert [u["id"] for u in updates] == ["u1", "u2"]
            assert mock_client.get.call_count == 2

    def test_returns_empty_list_when_no_updates(self) -> None:
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "incident_updates": [],
                "pagination_meta": {"page_size": 25, "after": None},
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            updates = _run_async(client.list_incident_updates("01ABC123"))

            assert updates == []


# =============================================================================
# list_all_incidents tests
# =============================================================================


class TestIncidentIOClientListAllIncidents(unittest.TestCase):
    """Tests for list_all_incidents method."""

    def test_list_all_incidents_single_page(self) -> None:
        """Test when all incidents fit in one page.

        Validation: Covers list_all_incidents method
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        # Return 2 incidents (less than page_size=100), indicating last page
        mock_api_response = {
            "incidents": [
                {
                    "id": "inc-1",
                    "reference": "INC-1",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
                {
                    "id": "inc-2",
                    "reference": "INC-2",
                    "severity": {"name": "Sev 2"},
                    "slack_channel_id": "C124",
                    "incident_status": {"name": "open"},
                    "visibility": "public",
                    "created_at": "2024-01-15T11:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents = _run_async(client.list_all_incidents(page_size=100))

            assert len(incidents) == 2
            # Single API call since results < page_size
            assert mock_client.get.call_count == 1

    def test_list_all_incidents_passes_through_updated_at_gte(self) -> None:
        """Test that updated_at_gte reaches the underlying list_incidents call.

        Validation: Covers list_all_incidents method
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        mock_api_response: dict[str, list[dict[str, Any]]] = {"incidents": []}

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            _run_async(
                client.list_all_incidents(page_size=100, updated_at_gte="2026-08-01")
            )

            call_kwargs = mock_client.get.call_args.kwargs
            assert call_kwargs["params"]["updated_at[gte]"] == "2026-08-01"

    def test_list_all_incidents_multiple_pages(self) -> None:
        """Test pagination across multiple pages.

        Validation: Covers pagination loop including cursor update (lines 456-457)
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        # Page 1: 2 incidents (equals page_size, so continue)
        page1_response = {
            "incidents": [
                {
                    "id": "inc-1",
                    "reference": "INC-1",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
                {
                    "id": "inc-2",
                    "reference": "INC-2",
                    "severity": {"name": "Sev 2"},
                    "slack_channel_id": "C124",
                    "incident_status": {"name": "open"},
                    "visibility": "public",
                    "created_at": "2024-01-15T11:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
            ]
        }

        # Page 2: 1 incident (less than page_size, so stop)
        page2_response = {
            "incidents": [
                {
                    "id": "inc-3",
                    "reference": "INC-3",
                    "severity": {"name": "Sev 3"},
                    "slack_channel_id": "C125",
                    "incident_status": {"name": "open"},
                    "visibility": "public",
                    "created_at": "2024-01-15T12:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response1 = MagicMock()
            mock_response1.status_code = 200
            mock_response1.json.return_value = page1_response
            mock_response1.raise_for_status = MagicMock()

            mock_response2 = MagicMock()
            mock_response2.status_code = 200
            mock_response2.json.return_value = page2_response
            mock_response2.raise_for_status = MagicMock()

            mock_client.get.side_effect = [mock_response1, mock_response2]

            incidents = _run_async(client.list_all_incidents(page_size=2))

            assert len(incidents) == 3
            assert mock_client.get.call_count == 2

            # Verify second call used cursor from first page
            second_call = mock_client.get.call_args_list[1]
            params = second_call.kwargs.get("params", second_call[1].get("params"))
            assert params["after"] == "inc-2"

    def test_list_all_incidents_with_last_recorded(self) -> None:
        """Test incremental fetch with last_recorded cursor.

        Validation: Covers cursor = last_recorded
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        mock_api_response = {
            "incidents": [
                {
                    "id": "inc-new",
                    "reference": "INC-NEW",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123",
                    "incident_status": {"name": "open"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents = _run_async(
                client.list_all_incidents(
                    page_size=100,
                    last_recorded="prev-incident-id",
                )
            )

            assert len(incidents) == 1
            # Verify last_recorded was passed as 'after' parameter
            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["after"] == "prev-incident-id"

    def test_list_all_incidents_empty(self) -> None:
        """Test listing when no incidents exist.

        Validation: Covers empty response handling
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            incidents = _run_async(client.list_all_incidents(page_size=100))

            assert incidents == []


# =============================================================================
# Sync wrapper tests (these are synchronous so TestCase is fine)
# =============================================================================


class TestIncidentIOClientSyncMethods(unittest.TestCase):
    """Tests for sync wrapper methods - exercises run_until_complete calls."""

    def test_sync_list_incidents(self) -> None:
        """Test sync wrapper for list_incidents.

        Validation: Covers line 519 (asyncio.get_event_loop().run_until_complete)
        """
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        mock_api_response = {
            "incidents": [
                {
                    "id": "01ABC123",
                    "reference": "INC-1234",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123456",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                }
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            # Call sync method - exercises run_until_complete
            incidents = client.list_incidents(page_size=100)

            assert len(incidents) == 1
            assert isinstance(incidents[0], IncidentWithRawFields)
            assert incidents[0].incident.incident_id == "01ABC123"

    def test_sync_list_all_incidents(self) -> None:
        """Test sync wrapper for list_all_incidents.

        Validation: Covers line 540 (asyncio.get_event_loop().run_until_complete)
        """
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        mock_api_response = {
            "incidents": [
                {
                    "id": "inc-1",
                    "reference": "INC-1",
                    "severity": {"name": "Sev 1"},
                    "slack_channel_id": "C123",
                    "incident_status": {"name": "closed"},
                    "visibility": "public",
                    "created_at": "2024-01-15T10:00:00Z",
                    "incident_timestamp_values": [],
                    "custom_field_entries": [],
                },
            ]
        }

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_api_response
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            # Call sync method - exercises run_until_complete
            incidents = client.list_all_incidents(page_size=100)

            assert len(incidents) == 1

    def test_sync_list_incidents_with_params(self) -> None:
        """Test sync list_incidents passes parameters correctly."""
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            client.list_incidents(after="cursor-123", page_size=50)

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["after"] == "cursor-123"
            assert params["page_size"] == 50

    def test_sync_list_all_incidents_with_last_recorded(self) -> None:
        """Test sync list_all_incidents passes last_recorded correctly."""
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"incidents": []}
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            client.list_all_incidents(page_size=50, last_recorded="last-inc-id")

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params"))
            assert params["after"] == "last-inc-id"

    def test_sync_get_incident(self) -> None:
        """Test sync wrapper for get_incident returns the raw payload untouched."""
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        raw_payload = {"incident": {"id": "01ABC123", "postmortem": {}}}

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = raw_payload
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            result = client.get_incident("01ABC123")

            assert result == raw_payload

    def test_sync_list_incident_updates(self) -> None:
        """Test sync wrapper for list_incident_updates returns oldest-first."""
        config = _create_config()
        client = IncidentIOClientSync(incidentio_config=config)

        with patch(
            "common.clients.incidentio_client.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "incident_updates": [
                    {"id": "u2", "message": "newer"},
                    {"id": "u1", "message": "older"},
                ],
                "pagination_meta": {"page_size": 25, "after": None},
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.get.return_value = mock_response

            updates = client.list_incident_updates("01ABC123")

            assert [u["id"] for u in updates] == ["u1", "u2"]


class TestIncidentIOClientTimestamps(unittest.TestCase):
    """Tests for timestamp extraction."""

    def test_extract_timestamps_all_present(self) -> None:
        """Test extraction of all timestamp types."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        timestamps = [
            {
                "incident_timestamp": {"name": "Reported at"},
                "value": {"value": "2024-01-15T10:00:00Z"},
            },
            {
                "incident_timestamp": {"name": "Accepted at"},
                "value": {"value": "2024-01-15T10:05:00Z"},
            },
            {
                "incident_timestamp": {"name": "Declined at"},
                "value": {"value": "2024-01-15T10:10:00Z"},
            },
            {
                "incident_timestamp": {"name": "Canceled at"},
                "value": {"value": "2024-01-15T11:00:00Z"},
            },
            {
                "incident_timestamp": {"name": "Resolved at"},
                "value": {"value": "2024-01-15T16:00:00Z"},
            },
            {
                "incident_timestamp": {"name": "Impact started at"},
                "value": {"value": "2024-01-15T09:00:00Z"},
            },
            {
                "incident_timestamp": {"name": "Closed at"},
                "value": {"value": "2024-01-15T18:00:00Z"},
            },
        ]

        result = client._extract_timestamps(timestamps)

        assert result["reported_at"] is not None
        assert result["accepted_at"] is not None
        assert result["declined_at"] is not None
        assert result["canceled_at"] is not None
        assert result["resolved_at"] is not None
        assert result["impact_started_at"] is not None
        assert result["closed_at"] is not None

    def test_extract_timestamps_partial(self) -> None:
        """Test extraction with only some timestamps present."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        timestamps = [
            {
                "incident_timestamp": {"name": "Reported at"},
                "value": {"value": "2024-01-15T10:00:00Z"},
            },
        ]

        result = client._extract_timestamps(timestamps)

        assert result["reported_at"] is not None
        assert result["accepted_at"] is None
        assert result["closed_at"] is None

    def test_extract_timestamps_empty(self) -> None:
        """Test extraction with empty timestamps."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        result = client._extract_timestamps([])

        assert all(v is None for v in result.values())

    def test_extract_timestamps_none(self) -> None:
        """Test extraction with None timestamps."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        result = client._extract_timestamps(None)

        assert all(v is None for v in result.values())

    def test_extract_timestamps_declined(self) -> None:
        """Test extraction of declined timestamp."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        timestamps = [
            {
                "incident_timestamp": {"name": "Declined at"},
                "value": {"value": "2024-01-15T10:00:00Z"},
            },
        ]

        result = client._extract_timestamps(timestamps)

        assert result["declined_at"] is not None
        assert result["declined_at"].year == 2024

    def test_extract_timestamps_skips_zero_year(self) -> None:
        """Test that zero/invalid timestamps are skipped."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        timestamps = [
            {
                "incident_timestamp": {"name": "Reported at"},
                "value": {"value": "0001-01-01T00:00:00Z"},
            },
        ]

        result = client._extract_timestamps(timestamps)

        assert result["reported_at"] is None


class TestIncidentIOClientCustomFields(unittest.TestCase):
    """Tests for custom field extraction."""

    def test_extract_custom_fields_impacted(self) -> None:
        """Test 'Who is impacted?' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Who is impacted?"},
                "values": [{"value_option": {"value": "Guest"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["impacted_parties"] == ["Guest"]

    def test_extract_custom_fields_impacted_multiple(self) -> None:
        """Test 'Who is impacted?' with multiple values."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Who is impacted?"},
                "values": [
                    {"value_option": {"value": "Guest"}},
                    {"value_option": {"value": "Host"}},
                    {"value_option": {"value": "Airfam"}},
                ],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["impacted_parties"] == ["Guest", "Host", "Airfam"]

    def test_extract_custom_fields_core_booking_yes(self) -> None:
        """Test 'Core Booking/Hosting Flow Impacted' with Yes."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Core Booking/Hosting Flow Impacted"},
                "values": [{"value_option": {"value": "Yes"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["core_booking_hosting_flow_impacted"] is True

    def test_extract_custom_fields_core_booking_no(self) -> None:
        """Test 'Core Booking/Hosting Flow Impacted' with No."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Core Booking/Hosting Flow Impacted"},
                "values": [{"value_option": {"value": "No"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["core_booking_hosting_flow_impacted"] is False

    def test_extract_custom_fields_core_booking_unknown_value(self) -> None:
        """Test 'Core Booking/Hosting Flow Impacted' with unknown value."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Core Booking/Hosting Flow Impacted"},
                "values": [{"value_option": {"value": "Unknown"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["core_booking_hosting_flow_impacted"] is None

    def test_extract_custom_fields_affected_services(self) -> None:
        """Test 'Affected Services' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Affected Services"},
                "values": [
                    {"value_catalog_entry": {"name": "service-a"}},
                    {"value_catalog_entry": {"name": "service-b"}},
                ],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["affected_services"] == ["service-a", "service-b"]

    def test_extract_custom_fields_environment(self) -> None:
        """Test 'Environment' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Environment"},
                "values": [{"value_option": {"value": "Production"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["environment"] == "Production"

    def test_extract_custom_fields_root_cause_service(self) -> None:
        """Test 'Root Cause Service' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Root Cause Service"},
                "values": [{"value_catalog_entry": {"name": "payment-service"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["root_cause_service"] == "payment-service"

    def test_extract_custom_fields_root_cause_change_type(self) -> None:
        """Test 'Root Cause Change Type' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Root Cause Change Type"},
                "values": [{"value_option": {"value": "Code Deploy"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["root_cause_change_type"] == "Code Deploy"

    def test_extract_custom_fields_detection_methods(self) -> None:
        """Test 'Incident detection method' field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Incident detection method"},
                "values": [{"value_option": {"value": "alert"}}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["detection_methods"] == ["alert"]

    def test_extract_custom_fields_detection_link(self) -> None:
        """Test 'Detection' link field extraction."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Detection"},
                "values": [{"value_link": "https://pagerduty.com/incident/123"}],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["detection_link"] == "https://pagerduty.com/incident/123"

    def test_extract_custom_fields_empty(self) -> None:
        """Test extraction with empty entries."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        result = client._extract_custom_fields([])

        assert all(v is None for v in result.values())

    def test_extract_custom_fields_none(self) -> None:
        """Test extraction with None entries."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        result = client._extract_custom_fields(None)

        assert all(v is None for v in result.values())

    def test_extract_custom_fields_empty_values(self) -> None:
        """Test extraction when values array is empty."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        entries = [
            {
                "custom_field": {"name": "Environment"},
                "values": [],
            }
        ]

        result = client._extract_custom_fields(entries)

        assert result["environment"] is None


class TestIncidentIOClientProcessIncident(unittest.TestCase):
    """Tests for _process_incident method."""

    def test_process_incident_full(self) -> None:
        """Test processing a full incident response."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        raw = {
            "id": "01FCNDV6P870EA6S7TK1DSYDG0",
            "name": "Database outage in us-east-1",
            "reference": "INC-1234",
            "severity": {"name": "Sev 1"},
            "slack_channel_id": "C1234567890",
            "incident_status": {"name": "closed", "category": "closed"},
            "visibility": "public",
            "created_at": "2024-01-15T10:00:00Z",
            "updated_at": "2024-01-15T18:00:00Z",
            "summary": "Database went down due to disk full",
            "incident_timestamp_values": [
                {
                    "incident_timestamp": {"name": "Reported at"},
                    "value": {"value": "2024-01-15T10:00:00Z"},
                },
                {
                    "incident_timestamp": {"name": "Accepted at"},
                    "value": {"value": "2024-01-15T10:05:00Z"},
                },
                {
                    "incident_timestamp": {"name": "Closed at"},
                    "value": {"value": "2024-01-15T18:00:00Z"},
                },
            ],
            "custom_field_entries": [
                {
                    "custom_field": {"name": "Who is impacted?"},
                    "values": [{"value_option": {"value": "Guest"}}],
                },
                {
                    "custom_field": {"name": "Environment"},
                    "values": [{"value_option": {"value": "Production"}}],
                },
            ],
        }

        result = client._process_incident(raw)

        assert isinstance(result, IncidentWithRawFields)
        incident = result.incident
        assert incident.incident_id == "01FCNDV6P870EA6S7TK1DSYDG0"
        assert incident.reference_id == "INC-1234"
        assert incident.severity == "Sev 1"
        assert incident.slack_channel_id == "C1234567890"
        assert incident.status == "closed"
        assert incident.visibility == "public"
        assert incident.status_category == "closed"
        assert incident.accepted_at is not None
        assert incident.closed_at is not None
        assert incident.impacted_parties == ["Guest"]
        assert incident.environment == "Production"
        # Raw fields on wrapper
        assert result.name == "Database outage in us-east-1"
        assert result.summary == "Database went down due to disk full"

    def test_process_incident_minimal(self) -> None:
        """Test processing an incident with minimal fields."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        raw = {
            "id": "01ABC",
            "reference": "INC-1",
            "severity": {"name": "Sev 3"},
            "slack_channel_id": "C123",
            "incident_status": {"name": "open"},
            "visibility": "private",
            "created_at": "2024-01-15T10:00:00Z",
        }

        result = client._process_incident(raw)

        assert isinstance(result, IncidentWithRawFields)
        incident = result.incident
        assert incident.incident_id == "01ABC"
        assert incident.reference_id == "INC-1"
        assert incident.severity == "Sev 3"
        assert incident.status == "open"
        assert incident.visibility == "private"
        assert incident.status_category is None  # No category in incident_status
        assert incident.reported_at is not None
        assert incident.updated_at is not None
        # Raw fields on wrapper
        assert result.name is None  # No name in raw data

    def test_process_incident_status_fallback(self) -> None:
        """Test status fallback to 'status' field when incident_status is missing."""
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        raw = {
            "id": "01ABC",
            "reference": "INC-1",
            "severity": {"name": "Sev 3"},
            "slack_channel_id": "C123",
            "status": "active",
            "visibility": "public",
            "created_at": "2024-01-15T10:00:00Z",
        }

        result = client._process_incident(raw)

        assert result.incident.status == "active"

    def test_process_incident_missing_created_at(self) -> None:
        """Test processing incident when created_at is missing.

        Validation: Covers line 304 (created_at fallback to utc_now_naive)
        """
        config = _create_config()
        client = IncidentIOClient(incidentio_config=config)

        raw = {
            "id": "01ABC",
            "reference": "INC-1",
            "severity": {"name": "Sev 3"},
            "slack_channel_id": "C123",
            "incident_status": {"name": "open"},
            "visibility": "public",
            # created_at is missing
        }

        result = client._process_incident(raw)

        # Should have a fallback timestamp
        incident = result.incident
        assert incident.created_at is not None
        assert incident.reported_at is not None
        assert incident.updated_at is not None


class TestIncidentIOClientFactory(unittest.TestCase):
    """Tests for factory functions."""

    def test_create_incidentio_client(self) -> None:
        """Test async client factory function."""
        config = _create_config()

        client = create_incidentio_client(config)

        assert isinstance(client, IncidentIOClient)
        assert client.incidentio_config == config
        assert client.incidentio_config.base_url == "https://api.incident.io"

    def test_create_incidentio_client_sync(self) -> None:
        """Test sync client factory function."""
        config = _create_config()

        client = create_incidentio_client_sync(config)

        assert isinstance(client, IncidentIOClientSync)
        assert client.incidentio_config == config

    def test_factory_with_custom_base_url(self) -> None:
        """Test factory with custom base URL via config."""
        config = _create_config(base_url="https://custom.api.com")

        client = create_incidentio_client(config)

        assert client.incidentio_config.base_url == "https://custom.api.com"

    def test_factory_with_custom_timeout(self) -> None:
        """Test factory with custom timeout via config."""
        config = _create_config(timeout=60.0)

        client = create_incidentio_client(config)

        assert client.incidentio_config.timeout == 60.0

    def test_factory_with_custom_backoff_delays(self) -> None:
        """Test factory with custom backoff delays via config."""
        config = _create_config(backoff_delays=[1.0, 2.0])

        client = create_incidentio_client(config)

        assert client.incidentio_config.backoff_delays == [1.0, 2.0]

    def test_factory_sync_with_custom_params(self) -> None:
        """Test sync factory with custom parameters via config."""
        config = _create_config(
            base_url="https://custom.api.com",
            timeout=60.0,
            backoff_delays=[1.0, 2.0],
        )

        client = create_incidentio_client_sync(config)

        assert client.incidentio_config.base_url == "https://custom.api.com"
        assert client.incidentio_config.timeout == 60.0
        assert client.incidentio_config.backoff_delays == [1.0, 2.0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
