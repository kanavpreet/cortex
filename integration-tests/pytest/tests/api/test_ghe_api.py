"""API contract tests: GitHub Enterprise (GHE) endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_ghe_trackers_reachable(api_client: httpx.Client) -> None:
    """GET /v1/ghe/trackers returns 200 with a list (may be empty)."""
    response = api_client.get("/v1/ghe/trackers")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data).__name__}"


def test_ghe_tracker_unknown_org_repo(api_client: httpx.Client) -> None:
    """GET /v1/ghe/tracker/{org_id}/{repo_id} for unknown IDs returns 200 with exists=False."""
    response = api_client.get("/v1/ghe/tracker/999999999/999999999")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data.get("exists") is False, (
        f"Expected exists=False for unknown tracker, got {data.get('exists')!r}"
    )
