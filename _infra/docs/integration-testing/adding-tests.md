# Adding UAT Tests — Developer Guide

## Where Tests Live

UAT tests are located at `integration-tests/pytest/` at the repository root, following the Pokey convention:

```
integration-tests/
└── pytest/
    ├── conftest.py          # Root: --environment, --cell, shared fixtures
    ├── config/
    │   ├── settings.py      # Pydantic Settings
    │   └── environments.py  # Environment registry
    ├── helpers/
    │   ├── api_client.py    # httpx wrapper
    │   └── wait.py          # poll_until()
    └── tests/
        ├── api/             # API contract tests
        └── llm/             # LLM integration tests
```

## Running Tests

See [Getting Started](getting-started.md) for instructions on running tests locally and in CI.

## Naming Conventions

### Files

| Category | File Pattern | Example |
|----------|-------------|---------|
| API | `tests/api/test_<service>_api.py` | `test_incidentio_api.py` |
| LLM | `tests/llm/test_<integration>.py` | `test_facade_connectivity.py` |

### Test Functions

```python
# Pattern: test_<subject>_<expected_behavior>
def test_health_returns_200(api_client):
    ...

def test_facade_returns_valid_completion(facade_client):
    ...
```

---

## Markers

Apply markers via the `pytestmark` module-level variable or `@pytest.mark.<marker>` decorator.

| Marker | When to Use | Included in Smoke |
|--------|-------------|------------------|
| `@pytest.mark.api` | All API contract tests | When also `smoke` |
| `@pytest.mark.llm` | LLM integration tests | No |
| `@pytest.mark.smoke` | Fast gate-check subset | Yes |

**Always apply at least one category marker.** Apply `smoke` additionally when the test is fast (<10s) and critical.

```python
import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def test_health_returns_200(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
```

---

## Available Fixtures

### Root fixtures (from `conftest.py`)

| Fixture | Type | Description |
|---------|------|-------------|
| `settings` | `Settings` | Resolved config for current environment |
| `environment` | `str` | Environment name (staging, sandbox, etc.) |
| `cell` | `str \| None` | Cell name if specified |

### `tests/api/conftest.py`

| Fixture | Type | Description |
|---------|------|-------------|
| `api_client` | `httpx.Client` | Pre-configured client for matik-api |
| `mcp_client` | `httpx.Client` | Pre-configured client for matik-mcp |

### `tests/llm/conftest.py`

| Fixture | Type | Description |
|---------|------|-------------|
| `facade_client` | `httpx.Client` | Pre-configured client for LLM Fusion Hub Facade |
| `bedrock_client` | `httpx.Client` | Pre-configured client for LLM Fusion Hub Bedrock |
| `api_client` | `httpx.Client` | Pre-configured client for matik-api |

---

## Templates

### API Test Template

```python
"""API contract tests for <service> endpoints."""

import pytest
import httpx

pytestmark = [pytest.mark.api]


def test_<endpoint>_returns_200(api_client: httpx.Client) -> None:
    """<endpoint> returns HTTP 200."""
    response = api_client.get("/<path>")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )


def test_<endpoint>_response_schema(api_client: httpx.Client) -> None:
    """<endpoint> response has expected fields."""
    response = api_client.get("/<path>")
    assert response.status_code == 200
    data = response.json()
    assert "expected_key" in data
```

### LLM Test Template

```python
"""LLM connectivity tests for <integration>."""

import pytest
import httpx

pytestmark = [pytest.mark.llm]


def test_<integration>_endpoint_reachable(facade_client: httpx.Client) -> None:
    """<integration> endpoint returns a non-5xx response."""
    response = facade_client.post("/<path>", json={...})
    assert response.status_code < 500, (
        f"Server error {response.status_code}: {response.text}"
    )
```

---

## Adding a New Test Category

If adding tests for a new service or integration:

1. Create `tests/<category>/conftest.py` with category-specific fixtures
2. Create `tests/<category>/test_<subject>.py` with your tests
3. Add the marker to `pytest.ini` in the `[pytest]` section:

```ini
markers =
    api: API contract tests
    llm: LLM integration tests
    smoke: Smoke tests (fast subset)
    <new_marker>: Description of new category
```

---

## CD Behavior

- **Pokey staging gate**: `api` + `smoke` markers run automatically after every staging deploy
- **On-demand locally**: Full suite including `llm` — run with `pytest tests/ --environment=staging`

To include your test in the smoke suite, add `@pytest.mark.smoke` and ensure it completes in under 10 seconds.
