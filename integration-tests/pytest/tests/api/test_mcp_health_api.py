"""API contract tests: MCP health endpoint on matik-api."""

import pytest
import httpx

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_mcp_health_returns_200(api_client: httpx.Client) -> None:
    """GET /v1/mcp/health returns HTTP 200."""
    response = api_client.get("/v1/mcp/health")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_mcp_health_schema(api_client: httpx.Client) -> None:
    """GET /v1/mcp/health response contains expected fields."""
    response = api_client.get("/v1/mcp/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data, f"Missing 'status' key. Got: {list(data.keys())}"
    assert "service" in data, f"Missing 'service' key. Got: {list(data.keys())}"
    assert "checks" in data, f"Missing 'checks' key. Got: {list(data.keys())}"


def test_mcp_health_database_ok(api_client: httpx.Client) -> None:
    """GET /v1/mcp/health shows database check as 'ok'."""
    response = api_client.get("/v1/mcp/health")
    assert response.status_code == 200
    data = response.json()
    checks = data.get("checks", {})
    assert checks.get("database") == "ok", (
        f"MCP health database check is not 'ok': {checks.get('database')!r}"
    )


def test_mcp_health_status_healthy(api_client: httpx.Client) -> None:
    """GET /v1/mcp/health returns status == 'healthy'."""
    response = api_client.get("/v1/mcp/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy", (
        f"Expected status='healthy', got {data.get('status')!r}"
    )
