"""API contract tests: Incident.io endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_get_tracker_returns_200(api_client: httpx.Client) -> None:
    """GET /v1/incidentio/incident/tracker/lastrecorded returns 200."""
    response = api_client.get("/v1/incidentio/incident/tracker/lastrecorded")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_get_incident_not_found(api_client: httpx.Client) -> None:
    """GET /v1/incidentio/incident/<nonexistent> returns 404."""
    response = api_client.get("/v1/incidentio/incident/INC-UAT-NONEXISTENT-9999")
    assert response.status_code == 404, (
        f"Expected 404, got {response.status_code}: {response.text}"
    )


def test_post_incident_hashes_empty_list(api_client: httpx.Client) -> None:
    """POST /v1/incidentio/incident/hashes with empty list returns 200 and empty dict."""
    response = api_client.post(
        "/v1/incidentio/incident/hashes",
        json={"incident_ids": []},
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data == {}, f"Expected empty dict, got {data!r}"


def test_post_incident_hashes_schema(api_client: httpx.Client) -> None:
    """POST /v1/incidentio/incident/hashes returns a dict response."""
    response = api_client.post(
        "/v1/incidentio/incident/hashes",
        json={"incident_ids": ["NONEXISTENT-9999"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"
