"""MCP server entry point.

Starts the Matik MCP server with both SSE and Streamable HTTP transports. The server:
1. Loads config and creates the API client
2. Fetches the OpenAPI spec to build tool definitions
3. Initializes the MCP Server with list_tools/call_tool handlers
4. Starts SSE (/sse + /messages/) and Streamable HTTP (/mcp) transports on the configured port
5. Refreshes the OpenAPI spec periodically in the background
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

from common.clients.matik_api_client import create_matik_api_client
from common.config import load_config, merge_config
from common.metrics.mcp_metrics import McpMetrics
from common.metrics.telescope import TelescopeClient
from common.utils import log_utils

from .proxy import ApiProxy
from .server import create_mcp_server
from .tools import McpToolRegistry

logger = log_utils.get_logger(__name__)

# Transports are imported here so they can be mocked in tests
from mcp.server.sse import SseServerTransport  # noqa: E402
from mcp.server.streamable_http_manager import (  # noqa: E402
    StreamableHTTPSessionManager,
)


async def _refresh_loop(
    registry: McpToolRegistry,
    interval_seconds: int,
    notify: Callable[[], Awaitable[None]],
) -> None:
    """Periodically refresh the OpenAPI spec in the background.

    When the tool set changes, sends notifications/tools/list_changed
    to all connected MCP clients so they re-fetch the tool list.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        logger.debug("refresh loop tick")
        changed = await asyncio.to_thread(registry.refresh)
        if changed:
            await notify()


def main() -> None:
    config = load_config("matik-mcp-config.yml")
    config = merge_config(config, "metrics.yml")

    log_utils.configure(
        level=config.common.log_level, environment=config.common.environment
    )

    logger.debug(
        "config loaded",
        api_endpoint=config.api.api_endpoint if config.api else None,
        port=config.common.port,
        refresh_minutes=(config.mcp.spec_refresh_interval_minutes if config.mcp else 5),
    )

    if config.api is None:
        raise RuntimeError("api config is required for MCP server (need api_endpoint)")

    # Initialize metrics (optional — no-op if telescope not configured)
    telescope: TelescopeClient | None = None
    mcp_metrics: McpMetrics | None = None
    if config.telescope and config.telescope.enabled:
        config.telescope.service_name = "mcp-server"
        config.telescope.environment = config.common.environment
        telescope = TelescopeClient(config.telescope)
        telescope.start()
        mcp_metrics = McpMetrics(telescope.meter)
        logger.info("metrics initialized", service="mcp-server")

    api_client = create_matik_api_client(api_config=config.api)

    refresh_interval_minutes = 5
    http_session_idle_timeout_seconds = 1800
    if config.mcp is not None:
        refresh_interval_minutes = config.mcp.spec_refresh_interval_minutes
        http_session_idle_timeout_seconds = config.mcp.http_session_idle_timeout_seconds

    registry = McpToolRegistry(api_client=api_client, metrics=mcp_metrics)
    proxy = ApiProxy(api_client=api_client)

    # Initial spec fetch
    try:
        tools = registry.fetch_and_parse()
        logger.info(
            "tool registry initialized",
            tool_count=len(tools),
            tools=[t.name for t in tools],
        )
    except Exception:
        logger.exception("failed to fetch initial OpenAPI spec")
        raise

    # Track active SSE sessions independently of the HTTP transport. The MCP
    # Server's notification WeakSet is shared across both transports, so it
    # cannot be used to count SSE sessions alone — we maintain the count at the
    # SSE transport boundary instead (see handle_sse).
    sse_session_count = {"value": 0}

    # Create the MCP server with handlers
    mcp_server, notify_tools_changed = create_mcp_server(
        registry=registry,
        proxy=proxy,
        metrics=mcp_metrics,
        http_session_count=lambda: len(http_session_manager._server_instances),
        sse_session_count=lambda: sse_session_count["value"],
    )
    logger.info("MCP server initialized")

    # Set up transports — both share the same mcp_server instance
    sse_transport = SseServerTransport("/messages/")
    http_session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=False,  # stateful so HTTP clients receive tools/list_changed notifications
        json_response=False,
        session_idle_timeout=http_session_idle_timeout_seconds,
    )

    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle SSE connection — runs the MCP server for a single client session.

        Maintains the live SSE session count so the active-sessions gauge is
        accurate per transport. The count is bumped on connect and decremented
        on disconnect, and the gauge is emitted at both edges so it stays fresh
        even between list_tools calls.
        """
        sse_session_count["value"] += 1
        if mcp_metrics:
            mcp_metrics.set_active_sessions(sse_session_count["value"], transport="sse")
        try:
            async with sse_transport.connect_sse(scope, receive, send) as streams:
                read_stream, write_stream = streams
                await mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp_server.create_initialization_options(),
                )
        finally:
            sse_session_count["value"] -= 1
            if mcp_metrics:
                mcp_metrics.set_active_sessions(
                    sse_session_count["value"], transport="sse"
                )

    # Starlette handles /messages/ routing and lifespan events (startup).
    # The /sse endpoint is routed at the ASGI level to avoid Starlette's
    # Route(endpoint=...) wrapping, which expects a Response return value
    # that conflicts with SSE's raw ASGI transport.
    # Start background spec refresh
    refresh_interval_seconds = refresh_interval_minutes * 60

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(http_session_manager.run())
            app.state.refresh_task = asyncio.create_task(
                _refresh_loop(registry, refresh_interval_seconds, notify_tools_changed)
            )
            try:
                yield
            finally:
                app.state.refresh_task.cancel()
                if telescope:
                    telescope.shutdown()

    starlette_app = Starlette(
        routes=[
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        lifespan=lifespan,
    )

    logger.debug("transport routes registered", routes=["/sse", "/messages/", "/mcp"])

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """Top-level ASGI app that routes /sse and /mcp directly, delegates the rest."""
        if scope["type"] == "http" and scope["path"] == "/sse":
            await handle_sse(scope, receive, send)
        elif scope["type"] == "http" and scope["path"] == "/mcp":
            await http_session_manager.handle_request(scope, receive, send)
        elif scope["type"] == "http" and scope["path"] == "/health":
            body = b'{"status": "ok"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"application/json"],
                        [b"content-length", str(len(body)).encode()],
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        else:
            await starlette_app(scope, receive, send)

    port = config.common.port
    logger.info("starting MCP server", port=port, transports=["sse", "streamable-http"])
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover - entry point guard
    main()
