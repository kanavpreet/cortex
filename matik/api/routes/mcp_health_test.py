"""Tests for MCP health check endpoint."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_engine
from api.routes.mcp_health import router


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the MCP health router."""
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


class TestMcpHealth:
    """Tests for GET /v1/mcp/health endpoint."""

    def test_returns_healthy_when_database_ok(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that /v1/mcp/health returns healthy when database is reachable."""
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        response = client.get("/v1/mcp/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "matik-api"
        assert data["checks"]["database"] == "ok"

    def test_returns_503_when_database_fails(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that /v1/mcp/health returns 503 when database is unreachable."""
        mock_engine.connect.return_value.__enter__.return_value.execute.side_effect = (
            Exception("Connection refused")
        )
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        response = client.get("/v1/mcp/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert "error" in data["checks"]["database"]

    def test_executes_select_1_query(
        self, app: FastAPI, mock_engine: MagicMock
    ) -> None:
        """Test that the health check executes SELECT 1."""
        app.dependency_overrides[get_engine] = lambda: mock_engine
        client = TestClient(app)

        client.get("/v1/mcp/health")

        conn = mock_engine.connect.return_value.__enter__.return_value
        conn.execute.assert_called_once()

    def test_operation_id_is_mcp_health(self, app: FastAPI) -> None:
        """Test that the endpoint has operationId for MCP tool discovery."""
        openapi = app.openapi()
        health_path = openapi["paths"].get("/v1/mcp/health", {})
        get_op = health_path.get("get", {})
        assert get_op.get("operationId") == "mcp_health"
