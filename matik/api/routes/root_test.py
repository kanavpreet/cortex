"""Tests for root endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.root import router


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with the root router."""
    app = FastAPI(
        title="Matik API",
        description="Test API description",
        version="1.0.0",
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


class TestRootEndpoint:
    """Tests for the root endpoint."""

    def test_root_returns_welcome_message(self, client: TestClient) -> None:
        """Test that root endpoint returns welcome message."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Welcome to Matik API"

    def test_root_returns_api_description(self, client: TestClient) -> None:
        """Test that root endpoint returns API description."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Test API description"

    def test_root_returns_api_version(self, client: TestClient) -> None:
        """Test that root endpoint returns API version."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.0.0"

    def test_root_response_structure(self, client: TestClient) -> None:
        """Test that root endpoint returns correct response structure."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"message", "description", "version"}
