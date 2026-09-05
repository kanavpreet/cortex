"""API contract tests: JIRA endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_jira_tracker_endpoint_reachable(api_client: httpx.Client) -> None:
    """GET /v1/jira/batch/tracker/tcmr returns 200 or 404 (no data yet)."""
    response = api_client.get("/v1/jira/batch/tracker/tcmr")
    assert response.status_code in (200, 404), (
        f"Expected 200 or 404, got {response.status_code}: {response.text}"
    )


def test_jira_tracker_invalid_type_returns_400(api_client: httpx.Client) -> None:
    """GET /v1/jira/batch/tracker/<invalid> returns 400."""
    response = api_client.get("/v1/jira/batch/tracker/nonexistent_type")
    assert response.status_code == 400, (
        f"Expected 400, got {response.status_code}: {response.text}"
    )


def test_jira_hashes_empty_list(api_client: httpx.Client) -> None:
    """POST /v1/jira/issues/hashes with empty list returns 200 and empty response."""
    response = api_client.post(
        "/v1/jira/issues/hashes",
        json={"issue_keys": []},
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data).__name__}"
    assert data == [], f"Expected empty list for empty input, got {data!r}"
