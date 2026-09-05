"""API contract tests: /health and /ready endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_health_returns_200(api_client: httpx.Client) -> None:
    """GET /health returns HTTP 200."""
    response = api_client.get("/health")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_health_response_schema(api_client: httpx.Client) -> None:
    """GET /health response contains expected fields."""
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data, f"Missing 'status' key. Got: {list(data.keys())}"
    assert "service" in data, f"Missing 'service' key. Got: {list(data.keys())}"


def test_health_status_value(api_client: httpx.Client) -> None:
    """GET /health returns status == 'healthy'."""
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy", (
        f"Expected status='healthy', got {data['status']!r}"
    )


def test_health_service_value(api_client: httpx.Client) -> None:
    """GET /health returns service == 'matik-api'."""
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "matik-api", (
        f"Expected service='matik-api', got {data['service']!r}"
    )


def test_ready_returns_200(api_client: httpx.Client) -> None:
    """GET /ready returns HTTP 200."""
    response = api_client.get("/ready")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_ready_response_schema(api_client: httpx.Client) -> None:
    """GET /ready response contains expected fields."""
    response = api_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data, f"Missing 'status' key. Got: {list(data.keys())}"
    assert "service" in data, f"Missing 'service' key. Got: {list(data.keys())}"
    assert "checks" in data, f"Missing 'checks' key. Got: {list(data.keys())}"


def test_ready_database_check_ok(api_client: httpx.Client) -> None:
    """GET /ready shows database check as 'ok'."""
    response = api_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    checks = data.get("checks", {})
    assert checks.get("database") == "ok", (
        f"Database check is not 'ok': {checks.get('database')!r}"
    )
