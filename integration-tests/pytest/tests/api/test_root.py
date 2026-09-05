"""API contract tests: GET / root endpoint."""

import pytest
import httpx

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_root_returns_200(api_client: httpx.Client) -> None:
    """GET / returns HTTP 200."""
    response = api_client.get("/")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_root_message(api_client: httpx.Client) -> None:
    """GET / returns the expected welcome message."""
    response = api_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data.get("message") == "Welcome to Matik API", (
        f"Unexpected message: {data.get('message')!r}"
    )


def test_root_has_version(api_client: httpx.Client) -> None:
    """GET / response includes a non-empty version field."""
    response = api_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data, f"Missing 'version' key. Got: {list(data.keys())}"
    assert data["version"], "version field is empty"


def test_root_has_description(api_client: httpx.Client) -> None:
    """GET / response includes a description field."""
    response = api_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "description" in data, (
        f"Missing 'description' key. Got: {list(data.keys())}"
    )
