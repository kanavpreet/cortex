"""LLM tests: Facade (Azure OAI proxy via LLM Fusion Hub) connectivity."""

import httpx
import pytest

from config.settings import Settings

pytestmark = [pytest.mark.llm]

FACADE_API_VERSION = "2024-12-01-preview"
FACADE_HEADERS = {
    "x-azure-region": "global",
    "x-azure-resource-bucket": "production",
}


def test_facade_endpoint_reachable(facade_client: httpx.Client, settings: Settings) -> None:
    """Facade proxy endpoint is network-reachable (not a connection error)."""
    response = facade_client.post(
        f"/chat/completions?api-version={FACADE_API_VERSION}",
        headers=FACADE_HEADERS,
        json={
            "model": settings.facade_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        },
    )
    # 200 = success, 4xx = auth/quota (network works), 5xx = investigate
    assert response.status_code < 500, (
        f"Facade returned server error {response.status_code}: {response.text}"
    )


def test_facade_returns_valid_completion(facade_client: httpx.Client, settings: Settings) -> None:
    """Facade proxy returns a valid chat completion response."""
    response = facade_client.post(
        f"/chat/completions?api-version={FACADE_API_VERSION}",
        headers=FACADE_HEADERS,
        json={
            "model": settings.facade_model,
            "messages": [{"role": "user", "content": "Reply with: ok"}],
            "max_tokens": 10,
        },
    )

    if response.status_code == 401:
        pytest.skip("Facade requires auth token — not available in this context")

    assert response.status_code == 200, (
        f"Facade returned {response.status_code}: {response.text}"
    )
    data = response.json()
    assert "choices" in data, (
        f"Facade response missing 'choices' key. Got: {list(data.keys())}"
    )
    assert len(data["choices"]) > 0, "Facade returned empty choices list"
