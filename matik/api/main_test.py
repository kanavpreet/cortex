"""Tests for API service entry point."""

from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response
from structlog.testing import capture_logs

from common.constants import (
    SERVICE_SIGNATURE_HEADER,
    SERVICE_TIMESTAMP_HEADER,
    TASK_ID_HEADER,
)
from common.utils import log_utils
from common.utils.service_auth import build_signature_headers


def _mock_config(with_mysql: bool = False) -> MagicMock:
    """Create a mock config object for testing."""
    mock = MagicMock()
    mock.common.environment = "local"
    mock.common.log_level = "INFO"
    mock.telescope = None  # Disable telescope for tests
    mock.enigmatologist = None  # Disable enigmatologist for tests
    if with_mysql:
        mock.mysql = MagicMock()  # Enable database
    else:
        mock.mysql = None  # Disable database for tests
    return mock


def test_health_endpoint_returns_healthy() -> None:
    """Test that the health endpoint returns healthy status."""
    with patch("api.main.load_config", return_value=_mock_config()):
        from api.main import app

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"service": "matik-api", "status": "healthy"}


def test_lifespan_logs_startup_message() -> None:
    """Test that lifespan logs the API startup message."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "api service started" in events


def test_lifespan_logs_shutdown_message() -> None:
    """Test that lifespan logs the API shutdown message."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "api service shutting down" in events


def test_lifespan_initializes_database_when_mysql_config_provided() -> None:
    """Test that database engine is initialized when mysql config is provided."""
    mock_engine = MagicMock()

    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config(with_mysql=True)),
        patch("api.main.create_long_lived_engine", return_value=mock_engine),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "database engine initialized" in events


def test_lifespan_disposes_database_engine_on_shutdown() -> None:
    """Test that database engine is disposed on shutdown."""
    mock_engine = MagicMock()

    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config(with_mysql=True)),
        patch("api.main.create_long_lived_engine", return_value=mock_engine),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "database engine disposed" in events
    mock_engine.dispose.assert_called_once()


def test_middleware_adds_task_id_to_response() -> None:
    """Test that middleware adds X-Task-ID header to response."""
    with patch("api.main.load_config", return_value=_mock_config()):
        from api.main import app

        client = TestClient(app)
        response = client.get("/health")

        assert TASK_ID_HEADER in response.headers
        # Should be {service}-{uuid} format (api-{36 char UUID})
        task_id = response.headers[TASK_ID_HEADER]
        assert task_id.startswith("api-")
        assert len(task_id) == len("api-") + 36  # service + hyphen + UUID


def test_middleware_preserves_incoming_task_id() -> None:
    """Test that middleware preserves incoming X-Task-ID header."""
    with patch("api.main.load_config", return_value=_mock_config()):
        from api.main import app

        client = TestClient(app)
        custom_id = "historian-abc123-def456"
        response = client.get("/health", headers={TASK_ID_HEADER: custom_id})

        assert response.headers[TASK_ID_HEADER] == custom_id


def test_middleware_sets_task_id_in_context() -> None:
    """Test that middleware sets task_id in logging context."""
    with patch("api.main.load_config", return_value=_mock_config()):
        from api.main import app

        client = TestClient(app)
        custom_id = "test-task-id-456"
        # Make request with custom ID
        client.get("/health", headers={TASK_ID_HEADER: custom_id})

        # Verify task_id was cleared after request (context cleanup)
        assert log_utils.get_task_id() is None


def test_middleware_skips_health_check_logging() -> None:
    """Test that middleware doesn't log health check requests."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        client = TestClient(app)
        client.get("/health")
        client.get("/ready")

    # Should not have "request started" logs for health endpoints
    request_logs = [log for log in cap_logs if log.get("event") == "request started"]
    health_logs = [
        log for log in request_logs if log.get("path") in ("/health", "/ready")
    ]
    assert len(health_logs) == 0


def test_lifespan_logs_warning_when_no_mysql_config() -> None:
    """Test that warning is logged when no mysql config is found."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config(with_mysql=False)),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "no mysql config found, database features disabled" in events


def test_lifespan_initializes_telescope_when_enabled() -> None:
    """Test that lifespan logs telescope initialized when _telescope is set."""
    mock_telescope = MagicMock()
    mock_telescope.is_enabled = True
    mock_telescope.meter = MagicMock()

    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
        # Patch module-level _telescope (telescope is initialized at module level)
        patch("api.main._telescope", mock_telescope),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "telescope metrics initialized" in events


def test_lifespan_shuts_down_telescope_on_exit() -> None:
    """Test that telescope is shut down properly."""
    mock_telescope = MagicMock()
    mock_telescope.is_enabled = True
    mock_telescope.meter = MagicMock()

    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
        # Patch module-level _telescope (telescope is initialized at module level)
        patch("api.main._telescope", mock_telescope),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "telescope metrics shutdown" in events
    mock_telescope.shutdown.assert_called_once()


def test_lifespan_logs_disabled_when_telescope_not_configured() -> None:
    """Test that log shows disabled when telescope is not configured."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        with TestClient(app):
            pass  # lifespan runs on enter/exit

    events = [log["event"] for log in cap_logs]
    assert "telescope metrics disabled or not configured" in events


def test_middleware_logs_non_health_requests() -> None:
    """Test that middleware logs requests to non-health endpoints."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        client = TestClient(app, raise_server_exceptions=False)
        # Make request to a non-health endpoint (will 404 but middleware still runs)
        client.get("/api/v1/some-endpoint")

    # Should have "request started" log for non-health endpoint
    request_logs = [log for log in cap_logs if log.get("event") == "request started"]
    assert len(request_logs) == 1
    assert request_logs[0]["path"] == "/api/v1/some-endpoint"
    assert request_logs[0]["method"] == "GET"


def test_middleware_logs_4xx_response_as_warning() -> None:
    """A non-exception 4xx response is logged with its status code."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/some-endpoint")

    assert response.status_code == 404
    error_logs = [
        log
        for log in cap_logs
        if log.get("event") == "request completed with error status"
    ]
    assert len(error_logs) == 1
    assert error_logs[0]["log_level"] == "warning"
    assert error_logs[0]["status_code"] == 404
    assert error_logs[0]["path"] == "/api/v1/some-endpoint"


def test_middleware_does_not_log_2xx_response() -> None:
    """Successful (2xx) responses produce no error-status log."""
    with (
        capture_logs() as cap_logs,
        patch("api.main.load_config", return_value=_mock_config()),
    ):
        from api.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/")

    assert response.status_code == 200
    error_logs = [
        log
        for log in cap_logs
        if log.get("event") == "request completed with error status"
    ]
    assert len(error_logs) == 0


def test_middleware_logs_exception_with_traceback_before_reraising() -> None:
    """An unhandled exception is logged with exc_info while task_id is still
    bound, then re-raised so the ASGI server can still produce a 500."""
    import asyncio
    from typing import NoReturn

    from starlette.requests import Request

    from api.main import TaskIDMiddleware

    middleware = TaskIDMiddleware(app=MagicMock())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/boom",
        "headers": [],
    }
    request = Request(scope)

    async def call_next(_: Request) -> NoReturn:
        raise RuntimeError("boom")

    async def run() -> None:
        with capture_logs() as cap_logs:
            try:
                await middleware.dispatch(request, call_next)
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError to propagate")

        failure_logs = [log for log in cap_logs if log.get("event") == "request failed"]
        assert len(failure_logs) == 1
        assert failure_logs[0]["path"] == "/boom"
        assert failure_logs[0]["method"] == "GET"
        # capture_logs records whether exc_info was requested for this event
        assert failure_logs[0]["exc_info"] is True

    asyncio.run(run())


def _make_request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> Request:
    """Build a Starlette Request with a working receive() for .body() reads."""
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    scope = {"type": "http", "method": method, "path": path, "headers": raw_headers}
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive=receive)


class TestServiceSignatureDispatch:
    """Test suite for create_service_signature_dispatch (Phase 1c)."""

    _SECRET = "test-shared-secret"

    @staticmethod
    async def _call_next(request: Request) -> Response:
        """Sentinel downstream handler recording that it was reached."""
        return Response(content="reached downstream", status_code=200)

    def test_no_secret_configured_passes_through(self) -> None:
        """secret=None (not provisioned in this env) is a total no-op."""
        import asyncio

        from api.main import create_service_signature_dispatch

        dispatch = create_service_signature_dispatch(secret=None, enforce=True)
        request = _make_request("POST", "/v1/mcp/incidentio")

        response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 200
        assert response.body == b"reached downstream"

    def test_exempt_path_bypasses_check_even_with_secret_and_enforce(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        dispatch = create_service_signature_dispatch(secret=self._SECRET, enforce=True)
        request = _make_request("GET", "/health")  # no signature headers at all

        response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 200

    def test_valid_signature_reaches_downstream(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        body = b'{"foo": "bar"}'
        headers = build_signature_headers(
            self._SECRET, "POST", "/v1/mcp/incidentio", body
        )
        dispatch = create_service_signature_dispatch(secret=self._SECRET, enforce=True)
        request = _make_request(
            "POST", "/v1/mcp/incidentio", headers=headers, body=body
        )

        response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 200
        assert response.body == b"reached downstream"

    def test_invalid_signature_rejected_when_enforced(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        dispatch = create_service_signature_dispatch(secret=self._SECRET, enforce=True)
        request = _make_request(
            "POST",
            "/v1/mcp/incidentio",
            headers={
                SERVICE_TIMESTAMP_HEADER: "100",
                SERVICE_SIGNATURE_HEADER: "sha256=not-a-real-signature",
            },
        )

        response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 401

    def test_missing_signature_rejected_when_enforced(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        dispatch = create_service_signature_dispatch(secret=self._SECRET, enforce=True)
        request = _make_request("POST", "/v1/mcp/incidentio")  # no headers at all

        response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 401

    def test_invalid_signature_logged_but_not_rejected_in_shadow_mode(self) -> None:
        """enforce=False: failures are logged but the request still proceeds."""
        import asyncio

        from api.main import create_service_signature_dispatch

        dispatch = create_service_signature_dispatch(secret=self._SECRET, enforce=False)
        request = _make_request("POST", "/v1/mcp/incidentio")  # no headers at all

        with capture_logs() as cap_logs:
            response = asyncio.run(dispatch(request, self._call_next))

        assert response.status_code == 200
        assert response.body == b"reached downstream"
        shadow_logs = [
            log
            for log in cap_logs
            if log.get("event")
            == "service signature check failed (shadow mode, not enforced)"
        ]
        assert len(shadow_logs) == 1
        assert shadow_logs[0]["log_level"] == "info"
        assert shadow_logs[0]["reason"] == "missing_headers"

    def test_records_valid_metric_on_success(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        metrics = MagicMock()
        body = b'{"foo": "bar"}'
        headers = build_signature_headers(
            self._SECRET, "POST", "/v1/mcp/incidentio", body
        )
        dispatch = create_service_signature_dispatch(
            secret=self._SECRET, enforce=True, metrics=metrics
        )
        request = _make_request(
            "POST", "/v1/mcp/incidentio", headers=headers, body=body
        )

        asyncio.run(dispatch(request, self._call_next))

        metrics.record.assert_called_once_with(valid=True, reason="", enforced=True)

    def test_records_invalid_metric_when_enforced(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        metrics = MagicMock()
        dispatch = create_service_signature_dispatch(
            secret=self._SECRET, enforce=True, metrics=metrics
        )
        request = _make_request("POST", "/v1/mcp/incidentio")  # no headers at all

        asyncio.run(dispatch(request, self._call_next))

        metrics.record.assert_called_once_with(
            valid=False, reason="missing_headers", enforced=True
        )

    def test_records_invalid_metric_in_shadow_mode(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        metrics = MagicMock()
        dispatch = create_service_signature_dispatch(
            secret=self._SECRET, enforce=False, metrics=metrics
        )
        request = _make_request("POST", "/v1/mcp/incidentio")  # no headers at all

        asyncio.run(dispatch(request, self._call_next))

        metrics.record.assert_called_once_with(
            valid=False, reason="missing_headers", enforced=False
        )

    def test_no_metric_recorded_for_exempt_path(self) -> None:
        """Health checks etc. would dominate the series — skip recording entirely."""
        import asyncio

        from api.main import create_service_signature_dispatch

        metrics = MagicMock()
        dispatch = create_service_signature_dispatch(
            secret=self._SECRET, enforce=True, metrics=metrics
        )
        request = _make_request("GET", "/health")

        asyncio.run(dispatch(request, self._call_next))

        metrics.record.assert_not_called()

    def test_no_metric_recorded_when_secret_unconfigured(self) -> None:
        import asyncio

        from api.main import create_service_signature_dispatch

        metrics = MagicMock()
        dispatch = create_service_signature_dispatch(
            secret=None, enforce=True, metrics=metrics
        )
        request = _make_request("POST", "/v1/mcp/incidentio")

        asyncio.run(dispatch(request, self._call_next))

        metrics.record.assert_not_called()
