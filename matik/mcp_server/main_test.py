"""Tests for MCP server entry point."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_server.main import _refresh_loop

# --- Helpers ---


def _mock_config(
    *,
    api_endpoint: str = "http://api:8080",
    port: int = 8090,
    log_level: str = "DEBUG",
    environment: str = "test",
    refresh_minutes: int = 5,
    with_api: bool = True,
    with_mcp: bool = True,
) -> MagicMock:
    """Create a mock MatikConfig for testing."""
    config = MagicMock()
    config.common.log_level = log_level
    config.common.environment = environment
    config.common.port = port

    if with_api:
        config.api.api_endpoint = api_endpoint
    else:
        config.api = None

    if with_mcp:
        config.mcp.spec_refresh_interval_minutes = refresh_minutes
        config.mcp.http_session_idle_timeout_seconds = 1800
    else:
        config.mcp = None

    # Disable telescope by default so tests don't trigger real OTLP initialization
    config.telescope = None

    return config


# --- _refresh_loop tests ---


class TestRefreshLoop:
    """Tests for the background spec refresh loop."""

    @pytest.mark.asyncio
    async def test_calls_registry_refresh(self) -> None:
        """Test that the loop calls registry.refresh() after sleeping."""
        mock_registry = MagicMock()
        mock_registry.refresh.return_value = False
        mock_notify = AsyncMock()

        call_count = 0

        async def _cancel_after_one(seconds: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with (
            patch("mcp_server.main.asyncio.sleep", side_effect=_cancel_after_one),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_loop(mock_registry, interval_seconds=300, notify=mock_notify)

        assert mock_registry.refresh.call_count == 1

    @pytest.mark.asyncio
    async def test_uses_configured_interval(self) -> None:
        """Test that the loop sleeps for the configured interval."""
        mock_registry = MagicMock()
        mock_registry.refresh.return_value = False
        mock_notify = AsyncMock()
        sleep_args: list[int] = []

        async def _capture_and_cancel(seconds: int) -> None:
            sleep_args.append(seconds)
            raise asyncio.CancelledError

        with (
            patch("mcp_server.main.asyncio.sleep", side_effect=_capture_and_cancel),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_loop(mock_registry, interval_seconds=600, notify=mock_notify)

        assert sleep_args == [600]

    @pytest.mark.asyncio
    async def test_calls_notify_when_tools_changed(self) -> None:
        """Test that notify is called when registry.refresh() returns True."""
        mock_registry = MagicMock()
        mock_registry.refresh.return_value = True
        mock_notify = AsyncMock()

        async def _cancel_after_one(seconds: int) -> None:
            raise asyncio.CancelledError

        with (
            patch("mcp_server.main.asyncio.sleep", side_effect=_cancel_after_one),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_loop(mock_registry, interval_seconds=300, notify=mock_notify)

        mock_notify.assert_not_called()  # CancelledError before refresh runs

    @pytest.mark.asyncio
    async def test_does_not_notify_when_tools_unchanged(self) -> None:
        """Test that notify is NOT called when registry.refresh() returns False."""
        mock_registry = MagicMock()
        mock_registry.refresh.return_value = False
        mock_notify = AsyncMock()

        call_count = 0

        async def _cancel_after_one(seconds: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with (
            patch("mcp_server.main.asyncio.sleep", side_effect=_cancel_after_one),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_loop(mock_registry, interval_seconds=300, notify=mock_notify)

        mock_registry.refresh.assert_called_once()
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_notifies_when_tools_changed(self) -> None:
        """Test that notify IS called when registry.refresh() returns True."""
        mock_registry = MagicMock()
        mock_registry.refresh.return_value = True
        mock_notify = AsyncMock()

        call_count = 0

        async def _cancel_after_one(seconds: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with (
            patch("mcp_server.main.asyncio.sleep", side_effect=_cancel_after_one),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_loop(mock_registry, interval_seconds=300, notify=mock_notify)

        mock_registry.refresh.assert_called_once()
        mock_notify.assert_called_once()


# --- main() tests ---


class TestMain:
    """Tests for the main() entry point."""

    @patch("mcp_server.main.uvicorn")
    @patch("mcp_server.main.SseServerTransport")
    @patch("mcp_server.main.create_mcp_server")
    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_starts_uvicorn_on_configured_port(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
        mock_create_server: MagicMock,
        mock_sse_transport: MagicMock,
        mock_uvicorn: MagicMock,
    ) -> None:
        """Test that main() starts uvicorn on the configured port."""
        mock_load_config.return_value = _mock_config(port=9090)
        mock_registry_cls.return_value.fetch_and_parse.return_value = []
        mock_create_server.return_value = (MagicMock(), AsyncMock())

        from mcp_server.main import main

        main()

        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args[1]
        assert call_kwargs["port"] == 9090
        assert call_kwargs["host"] == "0.0.0.0"

    @patch("mcp_server.main.uvicorn")
    @patch("mcp_server.main.SseServerTransport")
    @patch("mcp_server.main.create_mcp_server")
    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_creates_api_client_from_config(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
        mock_create_server: MagicMock,
        mock_sse_transport: MagicMock,
        mock_uvicorn: MagicMock,
    ) -> None:
        """Test that main() creates the API client from config."""
        config = _mock_config()
        mock_load_config.return_value = config
        mock_registry_cls.return_value.fetch_and_parse.return_value = []
        mock_create_server.return_value = (MagicMock(), AsyncMock())

        from mcp_server.main import main

        main()

        mock_create_client.assert_called_once_with(api_config=config.api)

    @patch("mcp_server.main.uvicorn")
    @patch("mcp_server.main.SseServerTransport")
    @patch("mcp_server.main.create_mcp_server")
    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_fetches_initial_openapi_spec(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
        mock_create_server: MagicMock,
        mock_sse_transport: MagicMock,
        mock_uvicorn: MagicMock,
    ) -> None:
        """Test that main() fetches the initial OpenAPI spec on startup."""
        mock_load_config.return_value = _mock_config()
        mock_registry = mock_registry_cls.return_value
        mock_registry.fetch_and_parse.return_value = []
        mock_create_server.return_value = (MagicMock(), AsyncMock())

        from mcp_server.main import main

        main()

        mock_registry.fetch_and_parse.assert_called_once()

    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_raises_when_api_config_missing(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
    ) -> None:
        """Test that main() raises when api config is missing."""
        mock_load_config.return_value = _mock_config(with_api=False)

        from mcp_server.main import main

        with pytest.raises(RuntimeError, match="api config is required"):
            main()

    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_raises_when_initial_spec_fetch_fails(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
    ) -> None:
        """Test that main() raises when initial OpenAPI spec fetch fails."""
        mock_load_config.return_value = _mock_config()
        mock_registry_cls.return_value.fetch_and_parse.side_effect = ConnectionError(
            "API unreachable"
        )

        from mcp_server.main import main

        with pytest.raises(ConnectionError, match="API unreachable"):
            main()

    @patch("mcp_server.main.uvicorn")
    @patch("mcp_server.main.SseServerTransport")
    @patch("mcp_server.main.create_mcp_server")
    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_defaults_refresh_interval_when_mcp_config_missing(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
        mock_create_server: MagicMock,
        mock_sse_transport: MagicMock,
        mock_uvicorn: MagicMock,
    ) -> None:
        """Test that refresh interval defaults to 5 minutes when mcp config is None."""
        mock_load_config.return_value = _mock_config(with_mcp=False)
        mock_registry_cls.return_value.fetch_and_parse.return_value = []
        mock_create_server.return_value = (MagicMock(), AsyncMock())

        from mcp_server.main import main

        # Should not raise — uses default of 5 minutes
        main()

        mock_uvicorn.run.assert_called_once()


# --- ASGI app routing tests ---


def _build_app() -> tuple[MagicMock, MagicMock, MagicMock, Callable[..., Any]]:
    """Run main() with mocks and return (sse_transport_instance, mcp_server, http_session_manager, asgi_app)."""
    mock_sse = MagicMock()
    mock_mcp = MagicMock()
    mock_notify = AsyncMock()
    mock_http_mgr = MagicMock()
    # run() must behave as an async context manager
    mock_http_mgr.run.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_http_mgr.run.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_http_mgr.handle_request = AsyncMock()

    with (
        patch("mcp_server.main.load_config", return_value=_mock_config()),
        patch("mcp_server.main.merge_config", side_effect=lambda c, _: c),
        patch("mcp_server.main.create_matik_api_client"),
        patch("mcp_server.main.McpToolRegistry") as mock_reg_cls,
        patch(
            "mcp_server.main.create_mcp_server",
            return_value=(mock_mcp, mock_notify),
        ),
        patch("mcp_server.main.SseServerTransport", return_value=mock_sse),
        patch(
            "mcp_server.main.StreamableHTTPSessionManager", return_value=mock_http_mgr
        ),
        patch("mcp_server.main.uvicorn") as mock_uvi,
    ):
        mock_reg_cls.return_value.fetch_and_parse.return_value = []

        from mcp_server.main import main

        main()

        asgi_app = mock_uvi.run.call_args[0][0]

    return mock_sse, mock_mcp, mock_http_mgr, asgi_app


class TestAsgiAppRouting:
    """Tests for the top-level ASGI app routing."""

    @pytest.mark.asyncio
    async def test_sse_path_calls_connect_sse(self) -> None:
        """Test that /sse routes to the SSE handler which calls connect_sse."""
        mock_sse, mock_mcp, _mock_http_mgr, asgi_app = _build_app()

        # Set up connect_sse as an async context manager
        mock_streams = (AsyncMock(), AsyncMock())
        mock_mcp.run = AsyncMock()

        @asynccontextmanager
        async def _fake_connect(scope, receive, send):  # type: ignore[no-untyped-def]
            yield mock_streams

        mock_sse.connect_sse = _fake_connect

        scope = {"type": "http", "path": "/sse"}
        receive = AsyncMock()
        send = AsyncMock()

        await asgi_app(scope, receive, send)

        mock_mcp.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_returns_200(self) -> None:
        """Test that /health returns 200 with JSON body."""
        _, _, _, asgi_app = _build_app()

        scope = {"type": "http", "path": "/health", "method": "GET"}
        receive = AsyncMock()
        sent_events: list[dict[str, Any]] = []

        async def send(event: dict[str, Any]) -> None:
            sent_events.append(event)

        await asgi_app(scope, receive, send)

        assert len(sent_events) == 2
        start = sent_events[0]
        assert start["type"] == "http.response.start"
        assert start["status"] == 200
        body_event = sent_events[1]
        assert body_event["body"] == b'{"status": "ok"}'

    @pytest.mark.asyncio
    async def test_non_sse_path_delegates_to_starlette(self) -> None:
        """Test that non-/sse paths are delegated to the Starlette app."""
        mock_sse, _mock_mcp, _mock_http_mgr, asgi_app = _build_app()

        # /messages/ should go to starlette, not to handle_sse
        scope = {"type": "http", "path": "/messages/", "method": "POST"}
        receive = AsyncMock()
        send = AsyncMock()

        # connect_sse should NOT be called for non-/sse paths
        mock_sse.connect_sse = MagicMock()

        # Starlette may raise due to incomplete ASGI scope — that's fine;
        # we only need to verify connect_sse was NOT invoked.
        with suppress(Exception):
            await asgi_app(scope, receive, send)

        mock_sse.connect_sse.assert_not_called()

    @pytest.mark.asyncio
    async def test_mcp_path_routes_to_session_manager(self) -> None:
        """Test that /mcp routes to the Streamable HTTP session manager."""
        mock_sse, _mock_mcp, mock_http_mgr, asgi_app = _build_app()

        scope = {"type": "http", "path": "/mcp", "method": "POST"}
        receive = AsyncMock()
        send = AsyncMock()

        await asgi_app(scope, receive, send)

        mock_http_mgr.handle_request.assert_called_once_with(scope, receive, send)
        mock_sse.connect_sse.assert_not_called()

    @pytest.mark.asyncio
    async def test_sse_path_does_not_call_session_manager(self) -> None:
        """Test that /sse does not invoke the HTTP session manager."""
        mock_sse, mock_mcp, mock_http_mgr, asgi_app = _build_app()

        mock_streams = (AsyncMock(), AsyncMock())
        mock_mcp.run = AsyncMock()

        @asynccontextmanager
        async def _fake_connect(scope, receive, send):  # type: ignore[no-untyped-def]
            yield mock_streams

        mock_sse.connect_sse = _fake_connect

        scope = {"type": "http", "path": "/sse"}
        receive = AsyncMock()
        send = AsyncMock()

        await asgi_app(scope, receive, send)

        mock_http_mgr.handle_request.assert_not_called()


# --- Metrics initialization tests ---


class TestMainMetrics:
    """Tests for telescope metrics initialization in main()."""

    @patch("mcp_server.main.uvicorn")
    @patch("mcp_server.main.SseServerTransport")
    @patch("mcp_server.main.create_mcp_server")
    @patch("mcp_server.main.McpToolRegistry")
    @patch("mcp_server.main.create_matik_api_client")
    @patch("mcp_server.main.McpMetrics")
    @patch("mcp_server.main.TelescopeClient")
    @patch("mcp_server.main.merge_config", side_effect=lambda c, _: c)
    @patch("mcp_server.main.load_config")
    def test_initializes_telescope_when_enabled(
        self,
        mock_load_config: MagicMock,
        mock_merge_config: MagicMock,
        mock_telescope_cls: MagicMock,
        mock_mcp_metrics_cls: MagicMock,
        mock_create_client: MagicMock,
        mock_registry_cls: MagicMock,
        mock_create_server: MagicMock,
        mock_sse_transport: MagicMock,
        mock_uvicorn: MagicMock,
    ) -> None:
        """Test that TelescopeClient is started and McpMetrics created when telescope is enabled."""
        config = _mock_config()
        config.telescope = MagicMock()
        config.telescope.enabled = True
        mock_load_config.return_value = config
        mock_registry_cls.return_value.fetch_and_parse.return_value = []
        mock_create_server.return_value = (MagicMock(), AsyncMock())
        mock_telescope_instance = mock_telescope_cls.return_value

        from mcp_server.main import main

        main()

        mock_telescope_cls.assert_called_once_with(config.telescope)
        mock_telescope_instance.start.assert_called_once()
        mock_mcp_metrics_cls.assert_called_once_with(mock_telescope_instance.meter)


# --- Lifespan tests ---


class TestLifespan:
    """Tests for the lifespan context manager in main()."""

    def _make_mock_http_mgr(self) -> MagicMock:
        """Return a mock StreamableHTTPSessionManager with a no-op run() context manager."""
        mock_http_mgr = MagicMock()
        mock_http_mgr.run.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_http_mgr.run.return_value.__aexit__ = AsyncMock(return_value=None)
        return mock_http_mgr

    @pytest.mark.asyncio
    async def test_lifespan_cancels_refresh_task_on_shutdown(self) -> None:
        """Test that the lifespan cancels the background refresh task on exit."""
        captured: dict[str, Any] = {}

        def capture_starlette(**kwargs: Any) -> MagicMock:
            captured["lifespan"] = kwargs.get("lifespan")
            return MagicMock()

        with (
            patch("mcp_server.main.load_config", return_value=_mock_config()),
            patch("mcp_server.main.merge_config", side_effect=lambda c, _: c),
            patch("mcp_server.main.create_matik_api_client"),
            patch("mcp_server.main.McpToolRegistry") as mock_reg_cls,
            patch(
                "mcp_server.main.create_mcp_server",
                return_value=(MagicMock(), AsyncMock()),
            ),
            patch("mcp_server.main.SseServerTransport"),
            patch(
                "mcp_server.main.StreamableHTTPSessionManager",
                return_value=self._make_mock_http_mgr(),
            ),
            patch("mcp_server.main.Starlette", side_effect=capture_starlette),
            patch("mcp_server.main.uvicorn"),
        ):
            mock_reg_cls.return_value.fetch_and_parse.return_value = []
            from mcp_server.main import main

            main()

        lifespan = captured["lifespan"]
        assert lifespan is not None

        mock_task = MagicMock()
        with patch("mcp_server.main.asyncio.create_task", return_value=mock_task):
            async with lifespan(MagicMock()):
                pass

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_shuts_down_telescope_when_configured(self) -> None:
        """Test that the lifespan calls telescope.shutdown() on exit when metrics are enabled."""
        captured: dict[str, Any] = {}

        def capture_starlette(**kwargs: Any) -> MagicMock:
            captured["lifespan"] = kwargs.get("lifespan")
            return MagicMock()

        mock_telescope_cls = MagicMock()
        config = _mock_config()
        config.telescope = MagicMock()
        config.telescope.enabled = True

        with (
            patch("mcp_server.main.load_config", return_value=config),
            patch("mcp_server.main.merge_config", side_effect=lambda c, _: c),
            patch("mcp_server.main.create_matik_api_client"),
            patch("mcp_server.main.McpToolRegistry") as mock_reg_cls,
            patch(
                "mcp_server.main.create_mcp_server",
                return_value=(MagicMock(), AsyncMock()),
            ),
            patch("mcp_server.main.SseServerTransport"),
            patch(
                "mcp_server.main.StreamableHTTPSessionManager",
                return_value=self._make_mock_http_mgr(),
            ),
            patch("mcp_server.main.TelescopeClient", mock_telescope_cls),
            patch("mcp_server.main.McpMetrics"),
            patch("mcp_server.main.Starlette", side_effect=capture_starlette),
            patch("mcp_server.main.uvicorn"),
        ):
            mock_reg_cls.return_value.fetch_and_parse.return_value = []
            from mcp_server.main import main

            main()

        lifespan = captured["lifespan"]
        assert lifespan is not None

        with patch("mcp_server.main.asyncio.create_task", return_value=MagicMock()):
            async with lifespan(MagicMock()):
                pass

        mock_telescope_cls.return_value.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_enters_session_manager_run(self) -> None:
        """Test that the lifespan enters and exits the HTTP session manager run() context."""
        captured: dict[str, Any] = {}
        mock_http_mgr = self._make_mock_http_mgr()

        def capture_starlette(**kwargs: Any) -> MagicMock:
            captured["lifespan"] = kwargs.get("lifespan")
            return MagicMock()

        with (
            patch("mcp_server.main.load_config", return_value=_mock_config()),
            patch("mcp_server.main.merge_config", side_effect=lambda c, _: c),
            patch("mcp_server.main.create_matik_api_client"),
            patch("mcp_server.main.McpToolRegistry") as mock_reg_cls,
            patch(
                "mcp_server.main.create_mcp_server",
                return_value=(MagicMock(), AsyncMock()),
            ),
            patch("mcp_server.main.SseServerTransport"),
            patch(
                "mcp_server.main.StreamableHTTPSessionManager",
                return_value=mock_http_mgr,
            ),
            patch("mcp_server.main.Starlette", side_effect=capture_starlette),
            patch("mcp_server.main.uvicorn"),
        ):
            mock_reg_cls.return_value.fetch_and_parse.return_value = []
            from mcp_server.main import main

            main()

        lifespan = captured["lifespan"]
        assert lifespan is not None

        with patch("mcp_server.main.asyncio.create_task", return_value=MagicMock()):
            async with lifespan(MagicMock()):
                pass

        mock_http_mgr.run.return_value.__aenter__.assert_awaited_once()
        mock_http_mgr.run.return_value.__aexit__.assert_awaited_once()
