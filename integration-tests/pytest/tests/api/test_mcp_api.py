"""API contract tests: MCP server endpoints (matik-mcp service)."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_mcp_server_health(mcp_client: httpx.Client) -> None:
    """GET /health on matik-mcp server returns HTTP 200."""
    response = mcp_client.get("/health")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_mcp_api_correlation_group_unknown_anchor(api_client: httpx.Client) -> None:
    """GET /v1/mcp/correlation/reliability/group/<unknown> returns 200 with null body."""
    response = api_client.get(
        "/v1/mcp/correlation/reliability/group/UAT-NONEXISTENT-ANCHOR-9999"
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    assert response.json() is None, (
        f"Expected null body for unknown anchor, got {response.json()!r}"
    )


def test_mcp_api_correlation_events_unknown_anchor(api_client: httpx.Client) -> None:
    """GET /v1/mcp/correlation/reliability/events/<unknown> returns empty events list."""
    response = api_client.get(
        "/v1/mcp/correlation/reliability/events/UAT-NONEXISTENT-ANCHOR-9999"
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data.get("events") == [], (
        f"Expected empty events list, got {data.get('events')!r}"
    )


def test_mcp_api_incidents_missing_params_returns_400(api_client: httpx.Client) -> None:
    """GET /v1/mcp/incidentio/incidents without params returns 400."""
    response = api_client.get("/v1/mcp/incidentio/incidents")
    assert response.status_code == 400, (
        f"Expected 400, got {response.status_code}: {response.text}"
    )
