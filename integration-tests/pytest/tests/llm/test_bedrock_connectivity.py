"""LLM tests: Bedrock proxy (via LLM Fusion Hub) connectivity."""

import httpx
import pytest

pytestmark = [pytest.mark.llm]

BEDROCK_MODEL = "global.anthropic.claude-sonnet-4-20250514-v1:0"


def test_bedrock_endpoint_reachable(bedrock_client: httpx.Client) -> None:
    """Bedrock proxy endpoint is network-reachable (not a connection error)."""
    response = bedrock_client.post(
        f"/model/{BEDROCK_MODEL}/invoke",
        json={
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert response.status_code < 500, (
        f"Bedrock proxy returned server error {response.status_code}: {response.text}"
    )


def test_bedrock_returns_valid_response(bedrock_client: httpx.Client) -> None:
    """Bedrock proxy returns a valid inference response."""
    response = bedrock_client.post(
        f"/model/{BEDROCK_MODEL}/invoke",
        json={
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Reply with: ok"}],
        },
    )

    if response.status_code == 401:
        pytest.skip("Bedrock proxy requires auth — not available in this context")

    assert response.status_code == 200, (
        f"Bedrock proxy returned {response.status_code}: {response.text}"
    )
    data = response.json()
    assert "content" in data or "choices" in data, (
        f"Bedrock response missing expected keys. Got: {list(data.keys())}"
    )
