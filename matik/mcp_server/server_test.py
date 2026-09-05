"""Tests for MCP server initialization and handlers."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as types
import pytest
from mcp.server.lowlevel import Server as LowLevelServer

from common.models.mcp_tool_definition import McpToolDefinition
from mcp_server.server import create_mcp_server

# --- Fixtures ---


@pytest.fixture
def sample_tools() -> list[McpToolDefinition]:
    """Sample tool definitions for testing."""
    return [
        McpToolDefinition(
            name="correlate_incident",
            description="Correlate an incident with related signals",
            input_schema={
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"},
                    "lookback_minutes": {"type": "integer"},
                },
                "required": ["incident_id"],
            },
            method="POST",
            path="/v1/mcp/correlate_incident",
        ),
        McpToolDefinition(
            name="mcp_health",
            description="Check API health",
            input_schema={"type": "object", "properties": {}},
            method="GET",
            path="/v1/mcp/health",
        ),
    ]


@pytest.fixture
def registry(sample_tools: list[McpToolDefinition]) -> MagicMock:
    """Mock registry with sample tools."""
    mock = MagicMock()
    mock.tools = sample_tools
    mock.get_tool.side_effect = lambda name: next(
        (t for t in sample_tools if t.name == name), None
    )
    return mock


@pytest.fixture
def proxy() -> MagicMock:
    """Mock proxy."""
    return MagicMock()


# --- Tests ---


class TestCreateMcpServer:
    """Tests for create_mcp_server."""

    def test_returns_server_and_notify_callback(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that create_mcp_server returns a Server and notify callback."""
        server, notify = create_mcp_server(registry=registry, proxy=proxy)
        assert isinstance(server, LowLevelServer)
        assert server.name == "matik-mcp-server"
        assert callable(notify)

    def test_registers_list_tools_handler(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that the server has a list_tools handler registered."""
        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        assert types.ListToolsRequest in server.request_handlers

    def test_registers_call_tool_handler(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that the server has a call_tool handler registered."""
        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        assert types.CallToolRequest in server.request_handlers


class TestListToolsHandler:
    """Tests for the list_tools handler."""

    @pytest.mark.asyncio
    async def test_returns_tools_from_registry(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that list_tools returns Tool objects from the registry."""
        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.ListToolsRequest]

        # Handler returns ServerResult wrapping the actual result
        server_result = await handler(types.ListToolsRequest(method="tools/list"))
        result = server_result.root
        assert isinstance(result, types.ListToolsResult)

        assert len(result.tools) == 2
        assert result.tools[0].name == "correlate_incident"
        assert (
            result.tools[0].description == "Correlate an incident with related signals"
        )
        assert result.tools[0].inputSchema == {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "lookback_minutes": {"type": "integer"},
            },
            "required": ["incident_id"],
        }
        assert result.tools[1].name == "mcp_health"


class TestCallToolHandler:
    """Tests for the call_tool handler."""

    @pytest.mark.asyncio
    async def test_forwards_call_via_proxy(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that call_tool forwards the request through the proxy."""
        response_data = {"correlation_id": "C-1", "signals": []}
        proxy.forward.return_value = json.dumps(response_data).encode()

        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.CallToolRequest]

        server_result = await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="correlate_incident",
                    arguments={"incident_id": "INC-123"},
                ),
            )
        )
        result = server_result.root
        assert isinstance(result, types.CallToolResult)

        proxy.forward.assert_called_once()
        call_args = proxy.forward.call_args
        assert call_args[0][0].name == "correlate_incident"
        assert call_args[0][1] == {"incident_id": "INC-123"}

        assert result.isError is not True
        assert len(result.content) == 1
        first = result.content[0]
        assert isinstance(first, types.TextContent)
        assert first.type == "text"
        assert json.loads(first.text) == response_data
        assert result.structuredContent == response_data

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_tool(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that call_tool returns an error for unknown tools."""
        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.CallToolRequest]

        server_result = await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="nonexistent_tool",
                    arguments={},
                ),
            )
        )
        result = server_result.root
        assert isinstance(result, types.CallToolResult)

        assert result.isError is True
        first = result.content[0]
        assert isinstance(first, types.TextContent)
        assert "Unknown tool: nonexistent_tool" in first.text
        proxy.forward.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_error_on_proxy_failure(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that call_tool returns an error when the proxy fails."""
        proxy.forward.side_effect = ConnectionError("connection refused")

        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.CallToolRequest]

        server_result = await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="correlate_incident",
                    arguments={"incident_id": "INC-123"},
                ),
            )
        )
        result = server_result.root
        assert isinstance(result, types.CallToolResult)

        assert result.isError is True
        first = result.content[0]
        assert isinstance(first, types.TextContent)
        assert "API error" in first.text

    @pytest.mark.asyncio
    async def test_handles_non_json_response(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that call_tool handles non-JSON API responses."""
        proxy.forward.return_value = b"plain text response"

        server, _ = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.CallToolRequest]

        server_result = await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="mcp_health",
                    arguments={},
                ),
            )
        )
        result = server_result.root
        assert isinstance(result, types.CallToolResult)

        assert result.isError is not True
        first = result.content[0]
        assert isinstance(first, types.TextContent)
        assert first.text == "plain text response"
        assert result.structuredContent is None


class TestActiveSessionCleanup:
    """Tests that disconnected sessions are automatically removed via WeakSet."""

    @pytest.mark.asyncio
    async def test_session_removed_after_gc(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Session count drops to zero once the session object is garbage collected."""
        import gc

        # AsyncMock holds internal self-references that prevent GC.
        # Use a plain class so we fully control the object's lifetime.
        class _FakeSession:
            async def send_tool_list_changed(self) -> None:
                pass

        mock_metrics = MagicMock()
        server, _ = create_mcp_server(
            registry=registry, proxy=proxy, metrics=mock_metrics
        )
        handler = server.request_handlers[types.ListToolsRequest]

        async def _call_list_tools_with(sess: _FakeSession) -> None:
            ctx = MagicMock()
            ctx.session = sess
            with patch("mcp_server.server.request_ctx") as rctx:
                rctx.get.return_value = ctx
                await handler(types.ListToolsRequest(method="tools/list"))
            # ctx and rctx go out of scope here; sess is the only remaining ref

        session = _FakeSession()
        await _call_list_tools_with(session)
        mock_metrics.set_active_sessions.assert_any_call(1, transport="sse")

        # Drop the only strong reference outside the helper and force GC
        mock_metrics.reset_mock()
        del session
        gc.collect()

        # Trigger another list_tools — WeakSet should now be empty, so count is 1 (just new session)
        new_session = _FakeSession()
        await _call_list_tools_with(new_session)

        mock_metrics.set_active_sessions.assert_any_call(1, transport="sse")
        assert all(
            c.args[0] != 2 for c in mock_metrics.set_active_sessions.call_args_list
        ), "expected GC'd session to not count toward active sessions"


class TestActiveSessionMetrics:
    """Tests for active session metric emission per transport."""

    @pytest.mark.asyncio
    async def test_emits_sse_transport_label(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        mock_metrics = MagicMock()
        server, _ = create_mcp_server(
            registry=registry, proxy=proxy, metrics=mock_metrics
        )
        handler = server.request_handlers[types.ListToolsRequest]

        mock_ctx = MagicMock()
        mock_ctx.session = AsyncMock()
        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        mock_metrics.set_active_sessions.assert_any_call(1, transport="sse")

    @pytest.mark.asyncio
    async def test_emits_http_transport_label_when_callable_provided(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        mock_metrics = MagicMock()
        server, _ = create_mcp_server(
            registry=registry,
            proxy=proxy,
            metrics=mock_metrics,
            http_session_count=lambda: 3,
        )
        handler = server.request_handlers[types.ListToolsRequest]

        mock_ctx = MagicMock()
        mock_ctx.session = AsyncMock()
        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        mock_metrics.set_active_sessions.assert_any_call(3, transport="http")

    @pytest.mark.asyncio
    async def test_sse_count_uses_callable_not_shared_weakset(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """SSE count comes from sse_session_count, not the shared WeakSet.

        The WeakSet is populated by every list_tools call regardless of
        transport, so an HTTP client calling list_tools must not inflate the
        SSE count. When sse_session_count is provided, its value is used
        verbatim for the sse transport label.
        """
        mock_metrics = MagicMock()
        server, _ = create_mcp_server(
            registry=registry,
            proxy=proxy,
            metrics=mock_metrics,
            http_session_count=lambda: 4,
            sse_session_count=lambda: 1,
        )
        handler = server.request_handlers[types.ListToolsRequest]

        # Simulate an HTTP client calling list_tools — it lands in the shared
        # WeakSet (size 1) but must not be reported as an SSE session.
        mock_ctx = MagicMock()
        mock_ctx.session = AsyncMock()
        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        mock_metrics.set_active_sessions.assert_any_call(1, transport="sse")
        mock_metrics.set_active_sessions.assert_any_call(4, transport="http")

    @pytest.mark.asyncio
    async def test_no_http_metric_without_callable(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        mock_metrics = MagicMock()
        server, _ = create_mcp_server(
            registry=registry, proxy=proxy, metrics=mock_metrics
        )
        handler = server.request_handlers[types.ListToolsRequest]

        mock_ctx = MagicMock()
        mock_ctx.session = AsyncMock()
        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        calls = mock_metrics.set_active_sessions.call_args_list
        assert all(c.kwargs.get("transport") != "http" for c in calls)


class TestNotifyToolsChanged:
    """Tests for the notify_tools_changed callback."""

    @pytest.mark.asyncio
    async def test_no_sessions_does_nothing(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that notify does nothing when no sessions are active."""
        _, notify = create_mcp_server(registry=registry, proxy=proxy)
        # Should not raise
        await notify()

    @pytest.mark.asyncio
    async def test_sends_to_active_session(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that notify sends list_changed to a captured session."""
        server, notify = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.ListToolsRequest]

        # Simulate a session being captured via list_tools
        mock_session = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.session = mock_session

        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        await notify()
        mock_session.send_tool_list_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_dead_sessions(
        self, registry: MagicMock, proxy: MagicMock
    ) -> None:
        """Test that dead sessions are discarded during notification."""
        server, notify = create_mcp_server(registry=registry, proxy=proxy)
        handler = server.request_handlers[types.ListToolsRequest]

        # Simulate a session that raises on notification (disconnected)
        dead_session = AsyncMock()
        dead_session.send_tool_list_changed.side_effect = ConnectionError("gone")
        mock_ctx = MagicMock()
        mock_ctx.session = dead_session

        with patch("mcp_server.server.request_ctx") as mock_request_ctx:
            mock_request_ctx.get.return_value = mock_ctx
            await handler(types.ListToolsRequest(method="tools/list"))

        # First call removes the dead session
        await notify()
        dead_session.send_tool_list_changed.assert_called_once()

        # Second call has no sessions to notify
        dead_session.reset_mock()
        await notify()
        dead_session.send_tool_list_changed.assert_not_called()
