"""Tests for the MCP API proxy."""

from unittest.mock import MagicMock

from common.models.mcp_tool_definition import McpToolDefinition
from mcp_server.proxy.api_proxy import ApiProxy, _resolve_path


class TestResolvePath:
    """Tests for path parameter resolution."""

    def test_no_path_params(self) -> None:
        """Test path without parameters passes through unchanged."""
        path, consumed = _resolve_path("/v1/mcp/health", {"foo": "bar"})
        assert path == "/v1/mcp/health"
        assert consumed == set()

    def test_single_path_param(self) -> None:
        """Test single path parameter is substituted."""
        path, consumed = _resolve_path(
            "/v1/mcp/incidents/{incident_id}",
            {"incident_id": "INC-123", "extra": "val"},
        )
        assert path == "/v1/mcp/incidents/INC-123"
        assert consumed == {"incident_id"}

    def test_multiple_path_params(self) -> None:
        """Test multiple path parameters are substituted."""
        path, consumed = _resolve_path(
            "/v1/mcp/{service}/{alert_id}",
            {"service": "payment", "alert_id": "42", "lookback": "60"},
        )
        assert path == "/v1/mcp/payment/42"
        assert consumed == {"service", "alert_id"}

    def test_missing_path_param_left_as_placeholder(self) -> None:
        """Test that missing path params are left as-is."""
        path, consumed = _resolve_path(
            "/v1/mcp/incidents/{incident_id}", {"other": "val"}
        )
        assert path == "/v1/mcp/incidents/{incident_id}"
        assert consumed == set()


class TestApiProxyForward:
    """Tests for ApiProxy.forward()."""

    def test_get_request_sends_query_params(self) -> None:
        """Test GET requests send remaining args as query parameters."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b'{"status": "ok"}'

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="summarize_incident",
            description="Summarize an incident",
            method="GET",
            path="/v1/mcp/summarize_incident",
        )

        result = proxy.forward(tool, {"incident_id": "INC-123"})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/summarize_incident",
            params={"incident_id": "INC-123"},
            headers=None,
        )
        assert result == b'{"status": "ok"}'

    def test_post_request_sends_json_body(self) -> None:
        """Test POST requests send remaining args as JSON body."""
        mock_client = MagicMock()
        mock_client.json_request.return_value = b'{"correlation_id": "C-1"}'

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="correlate_incident",
            description="Correlate an incident",
            method="POST",
            path="/v1/mcp/correlate_incident",
        )

        result = proxy.forward(tool, {"incident_id": "INC-123", "lookback_minutes": 60})

        mock_client.json_request.assert_called_once_with(
            "POST",
            "/v1/mcp/correlate_incident",
            body={"incident_id": "INC-123", "lookback_minutes": 60},
            headers=None,
        )
        assert result == b'{"correlation_id": "C-1"}'

    def test_path_params_consumed_before_forwarding(self) -> None:
        """Test that path params are resolved and not sent as query/body params."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="get_incident",
            description="Get incident detail",
            method="GET",
            path="/v1/mcp/incidents/{incident_id}",
        )

        proxy.forward(tool, {"incident_id": "INC-123", "fields": "summary"})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/incidents/INC-123",
            params={"fields": "summary"},
            headers=None,
        )

    def test_headers_forwarded(self) -> None:
        """Test that additional headers are forwarded to the API."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="test_tool",
            description="Test",
            method="GET",
            path="/v1/mcp/test",
        )

        headers = {"X-MCP-Session-ID": "sess-abc", "X-User-ID": "user-1"}
        proxy.forward(tool, {}, headers=headers)

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/test",
            params=None,
            headers=headers,
        )

    def test_get_with_no_remaining_args(self) -> None:
        """Test GET request with no remaining args sends no query params."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="test_tool",
            description="Test",
            method="GET",
            path="/v1/mcp/test",
        )

        proxy.forward(tool, {})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/test",
            params=None,
            headers=None,
        )

    def test_post_with_no_remaining_args(self) -> None:
        """Test POST request with no remaining args sends no body."""
        mock_client = MagicMock()
        mock_client.json_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="test_tool",
            description="Test",
            method="POST",
            path="/v1/mcp/test",
        )

        proxy.forward(tool, {})

        mock_client.json_request.assert_called_once_with(
            "POST",
            "/v1/mcp/test",
            body=None,
            headers=None,
        )

    def test_put_request_sends_json_body(self) -> None:
        """Test PUT requests send remaining args as JSON body with PUT method."""
        mock_client = MagicMock()
        mock_client.json_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="update_tool",
            description="Update something",
            method="PUT",
            path="/v1/mcp/resource/{id}",
        )

        proxy.forward(tool, {"id": "42", "name": "updated"})

        mock_client.json_request.assert_called_once_with(
            "PUT",
            "/v1/mcp/resource/42",
            body={"name": "updated"},
            headers=None,
        )

    def test_get_request_list_param_passed_as_list(self) -> None:
        """Test that list query params are passed as lists, not stringified.

        When the LLM passes an array argument (e.g. reference_ids=["INC-1"]),
        the proxy must preserve the list so httpx serializes it as repeated
        query params (?reference_ids=INC-1) rather than the Python repr string
        "['INC-1']", which would cause the API to return empty results.
        """
        mock_client = MagicMock()
        mock_client.get_request.return_value = b'{"incidents": []}'

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="mcp_get_incidentio_incidents",
            description="Get incidents",
            method="GET",
            path="/v1/mcp/incidentio/incidents",
        )

        proxy.forward(tool, {"reference_ids": ["INC-5267", "INC-5268"]})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/incidentio/incidents",
            params={"reference_ids": ["INC-5267", "INC-5268"]},
            headers=None,
        )

    def test_get_request_mixed_scalar_and_list_params(self) -> None:
        """Test that scalar params are stringified and list params are preserved."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="test_tool",
            description="Test",
            method="GET",
            path="/v1/mcp/test",
        )

        proxy.forward(tool, {"ids": ["A", "B"], "limit": 10})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/test",
            params={"ids": ["A", "B"], "limit": "10"},
            headers=None,
        )

    def test_delete_request_sends_query_params(self) -> None:
        """Test DELETE requests send remaining args as query parameters."""
        mock_client = MagicMock()
        mock_client.get_request.return_value = b"{}"

        proxy = ApiProxy(api_client=mock_client)
        tool = McpToolDefinition(
            name="delete_tool",
            description="Delete something",
            method="DELETE",
            path="/v1/mcp/resource/{id}",
        )

        proxy.forward(tool, {"id": "42"})

        mock_client.get_request.assert_called_once_with(
            "/v1/mcp/resource/42",
            params=None,
            headers=None,
        )
