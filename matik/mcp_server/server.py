"""MCP server initialization — creates the low-level Server with tool handlers.

Bridges the MCP protocol to the Matik API by registering:
- list_tools: returns the current tools from the registry
- call_tool: forwards requests to the API via the proxy

Uses the low-level Server (not FastMCP) because tool definitions are
dynamically generated from the OpenAPI spec, not statically declared
with decorators.
"""

import asyncio
import json
import weakref
from collections.abc import Awaitable, Callable
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import request_ctx
from mcp.server.session import ServerSession

from common.metrics.mcp_metrics import McpMetrics
from common.utils import log_utils

from .proxy import ApiProxy
from .tools import McpToolRegistry

logger = log_utils.get_logger(__name__)


def create_mcp_server(
    registry: McpToolRegistry,
    proxy: ApiProxy,
    metrics: McpMetrics | None = None,
    http_session_count: Callable[[], int] | None = None,
    sse_session_count: Callable[[], int] | None = None,
) -> tuple[Server, Callable[[], Awaitable[None]]]:
    """Create and configure an MCP Server with tool handlers.

    Args:
        registry: Tool registry that holds current tool definitions.
        proxy: API proxy that forwards tool calls to the Matik API.
        metrics: Optional McpMetrics instance for transport instrumentation.
        http_session_count: Optional callable that returns the current number
            of active HTTP streamable sessions. When provided, the active
            session metric is emitted with transport="http".
        sse_session_count: Optional callable that returns the current number of
            active SSE sessions. When provided, the active session metric for
            transport="sse" uses this value. When omitted, it falls back to the
            size of the internal notification WeakSet — but that set is shared
            across transports (both SSE and HTTP clients call list_tools), so
            the fallback over-counts when HTTP clients are connected. Callers
            that serve both transports should always pass this so SSE and HTTP
            are counted independently.

    Returns:
        Tuple of (server, notify_tools_changed). The server is a configured
        mcp.server.lowlevel.Server ready to be connected to a transport.
        notify_tools_changed is an async callback that sends
        notifications/tools/list_changed to all connected clients.
    """
    server = Server(name="matik-mcp-server")
    # Tracks every session that has called list_tools, regardless of transport,
    # so notify_tools_changed can reach both SSE and HTTP clients. It is NOT a
    # reliable per-transport session count — use the *_session_count callables
    # for that (see _emit_session_metrics).
    _active_sessions: weakref.WeakSet[ServerSession] = weakref.WeakSet()

    def _emit_session_metrics() -> None:
        """Emit active-session gauges per transport.

        SSE and HTTP are counted from their own transport-specific callables so
        a session is never counted under more than one transport. Only the SSE
        count falls back to the shared notification WeakSet when no callable is
        provided (e.g. in tests).
        """
        if not metrics:
            return
        sse_count = (
            sse_session_count()
            if sse_session_count is not None
            else len(_active_sessions)
        )
        metrics.set_active_sessions(sse_count, transport="sse")
        if http_session_count is not None:
            metrics.set_active_sessions(http_session_count(), transport="http")

    @server.list_tools()  # type: ignore[no-untyped-call]
    async def list_tools() -> list[types.Tool]:
        """Return the current tool definitions from the registry.

        Tools are dynamically generated from the Matik API's OpenAPI spec,
        so this list may change between calls if the spec is refreshed.
        """
        # Capture the session so we can send notifications later
        try:
            ctx = request_ctx.get()
            _active_sessions.add(ctx.session)
        except LookupError:
            pass  # Called outside request context (e.g., in tests)

        _emit_session_metrics()

        tools = [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
            )
            for tool in registry.tools
        ]
        logger.debug("listing tools", tool_count=len(tools))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """Forward a tool call to the Matik API via the proxy.

        Looks up the tool definition in the registry, delegates to the
        proxy for HTTP forwarding, and wraps the response as an MCP result.
        """
        logger.debug("call_tool received", tool=name, arguments=arguments)
        tool = registry.get_tool(name)
        if tool is None:
            logger.warning("tool not found", tool=name)
            if metrics:
                # No duration_seconds: the tool was rejected before any work,
                # so there is no meaningful latency to record. Passing a
                # synthetic 0.0 would skew the duration percentiles.
                metrics.record_tool_call(name, "error", error_type="unknown_tool")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

        record = metrics.start_tool_call(name) if metrics else None

        try:
            # Proxy.forward is synchronous (uses httpx.Client), so
            # run it in a thread to avoid blocking the event loop.
            raw = await asyncio.to_thread(proxy.forward, tool, arguments)

            if record:
                record("success")

            response_text = raw.decode("utf-8")

            # Try to return structured content if response is valid JSON
            try:
                structured = json.loads(response_text)
                logger.debug(
                    "call_tool completed",
                    tool=name,
                    response_bytes=len(raw),
                    structured=True,
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=response_text)],
                    structuredContent=structured,
                )
            except json.JSONDecodeError:
                logger.debug(
                    "call_tool completed",
                    tool=name,
                    response_bytes=len(raw),
                    structured=False,
                )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=response_text)],
                )

        except Exception as e:
            if record:
                record("error", type(e).__name__)
            logger.error("tool call failed", tool=name, error=str(e))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"API error: {e}")],
                isError=True,
            )

    async def notify_tools_changed() -> None:
        """Send notifications/tools/list_changed to all connected clients.

        Sessions that have been GC'd (transport closed) are already absent from
        the WeakSet. Sessions that are still alive but fail to receive the
        notification are explicitly discarded here.
        """
        if not _active_sessions:
            logger.debug("no active sessions to notify")
            return

        logger.info(
            "notifying clients of tool list change", session_count=len(_active_sessions)
        )
        for session in list(_active_sessions):
            try:
                await session.send_tool_list_changed()
            except Exception:
                logger.debug("removing dead session during notification")
                _active_sessions.discard(session)

        _emit_session_metrics()

    return server, notify_tools_changed
