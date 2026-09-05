"""Tests for health check endpoints."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from api.routes.deps import get_engine
from api.routes.health import router


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the health router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_engine() -> MagicMock:
    """Create a mock SQLAlchemy engine."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None
    return engine


class TestGetHealth:
    """Tests for GET /health endpoint."""

    def test_returns_healthy_status(self, app: FastAPI) -> None:
        """Test that /health returns healthy status."""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "matik-api"}

    def test_does_not_require_database(self, app: FastAPI) -> None:
        """Test that /health works without database dependency."""
        # No engine override needed - health endpoint doesn't use it
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200


class TestGetReadiness:
    """Tests for GET /ready endpoint."""

    def test_returns_ready_when_database_healthy(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that /ready returns ready status when database is healthy."""
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["service"] == "matik-api"
        assert data["checks"]["database"] == "ok"

    def test_returns_503_when_database_fails(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that /ready returns 503 when database check fails."""
        # Make the database connection fail
        mock_engine.connect.return_value.__enter__.return_value.execute.side_effect = (
            Exception("Connection failed")
        )
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert "error" in data["checks"]["database"]

    def test_executes_select_1_query(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that /ready executes SELECT 1 to check database."""
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        client.get("/ready")

        # Verify execute was called
        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.assert_called_once()

    def test_database_failure_logs_with_exc_info(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """The DB-check failure is logged via logger.exception (exc_info),
        not a bare f-string, so a traceback is captured."""
        mock_engine.connect.return_value.__enter__.return_value.execute.side_effect = (
            Exception("Connection failed")
        )
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        with capture_logs() as cap_logs:
            client.get("/ready")

        failure_logs = [
            log
            for log in cap_logs
            if log.get("event") == "database health check failed"
        ]
        assert len(failure_logs) == 1
        assert failure_logs[0]["error"] == "Connection failed"
        assert failure_logs[0]["exc_info"] is True

    def test_readiness_failure_logs_structured_checks(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """The readiness-failed warning carries `checks` as a structured
        kwarg (not stdlib's `extra=`)."""
        mock_engine.connect.return_value.__enter__.return_value.execute.side_effect = (
            Exception("Connection failed")
        )
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        with capture_logs() as cap_logs:
            client.get("/ready")

        warning_logs = [
            log for log in cap_logs if log.get("event") == "readiness check failed"
        ]
        assert len(warning_logs) == 1
        assert "error" in warning_logs[0]["checks"]["database"]
