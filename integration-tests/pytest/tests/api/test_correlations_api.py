"""API contract tests: correlation endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_correlation_events_empty_for_unknown_anchor(api_client: httpx.Client) -> None:
    """GET /v1/correlation/reliability/events/<unknown> returns 200 and empty list."""
    response = api_client.get(
        "/v1/correlation/reliability/events/UAT-NONEXISTENT-ANCHOR-9999"
    )
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert isinstance(data, list), f"Expected list, got {type(data).__name__}"
    assert data == [], f"Expected empty list, got {data!r}"
