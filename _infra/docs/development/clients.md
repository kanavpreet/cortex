# API Clients Design Guide

## Overview

API clients provide a clean abstraction layer for external service interactions in the Matik platform. This guide documents the patterns established during the migration from Go to Python.

### Technology Stack

- **httpx**: HTTP client library for sync/async requests
- **githubkit**: GitHub API client library (for GHE)
- **openai**: OpenAI SDK (for Facade/LLM)
- **dataclasses**: Python dataclasses for client configuration
- **Location:** `matik/common/clients/`

### Design Principles

1. **Configuration-driven**: Clients accept config objects, not raw parameters
2. **Factory functions**: Provide `create_*_client()` functions for clean instantiation
3. **Graceful error handling**: Log errors, return meaningful values
4. **Retry logic**: Include retry with exponential backoff for transient failures
5. **Environment awareness**: Handle local vs Kubernetes authentication differences

---

## File Structure

```
matik/common/clients/
├── __init__.py              # Module exports
├── facade_client.py         # LLM/Facade client (async + sync)
├── ghe_client.py            # GitHub Enterprise client (async + sync)
├── greenroom_client.py      # Backstage/Greenroom client (sync)
├── jira_client.py           # JIRA client (sync)
└── matik_api_client.py      # Internal Matik API client (sync)
```

**Principle:** One file per external service, containing both client class and factory function.

---

## Module Exports (`__init__.py`)

All clients must be exported from `common/clients/__init__.py`:

```python
"""Common client libraries for the Matik platform."""

from common.clients.facade_client import (
    FacadeClient,
    FacadeClientSync,
    FacadeMessage,
    create_facade_client,
    create_facade_client_sync,
    facade_assistant_message,
    facade_system_message,
    facade_user_message,
)
from common.clients.ghe_client import (
    GHEClient,
    GHEClientSync,
    create_ghe_client,
    create_ghe_client_sync,
)
from common.clients.greenroom_client import (
    GreenroomClient,
    create_greenroom_client,
)

__all__ = [
    "FacadeClient",
    "FacadeClientSync",
    "GHEClient",
    "GHEClientSync",
    "GreenroomClient",
    "create_facade_client",
    "create_facade_client_sync",
    "create_ghe_client",
    "create_ghe_client_sync",
    "create_greenroom_client",
    # ...
]
```

---

## Implementation Patterns

### Client Class Structure

Use Python dataclasses with config objects:

```python
from dataclasses import dataclass, field
from common.models.my_config import MyConfig
from common.models.common_config import CommonConfig

@dataclass
class MyClient:
    """Client for interacting with My Service API.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yaml")
        client = MyClient(
            my_config=config.my_service,
            common_config=config.common,
        )

        result = client.get_data()
    """

    my_config: MyConfig
    common_config: CommonConfig | None = None

    # Private fields (not constructor parameters)
    _base_url: str = field(init=False)
    _headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the client after dataclass construction."""
        self._base_url = self.my_config.base_url.rstrip("/")
        self._headers = self._build_headers()
        logger.info("Created MyClient for: %s", self._base_url)
```

**Key points:**
- Use `@dataclass` decorator
- Config objects as constructor parameters
- Private fields use `field(init=False)`
- Initialization logic in `__post_init__`
- Include usage example in docstring

### Factory Functions

Provide factory functions for clean instantiation:

```python
def create_my_client(
    my_config: MyConfig,
    common_config: CommonConfig | None = None,
) -> MyClient:
    """Create a MyClient from configuration objects.

    Args:
        my_config: MyConfig from MatikConfig.my_service
        common_config: CommonConfig from MatikConfig.common (optional)

    Returns:
        Configured MyClient instance.

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yaml")
        client = create_my_client(
            my_config=config.my_service,
            common_config=config.common,
        )
    """
    return MyClient(
        my_config=my_config,
        common_config=common_config,
    )
```

### Environment Detection

Use `is_local_environment()` for environment-specific behavior:

```python
from common.utils.env_utils import is_local_environment

def __post_init__(self) -> None:
    is_local = is_local_environment(self.common_config)

    if is_local:
        # Local development: Use IAP token and public endpoint
        self._host = "https://service.a.musta.ch"
        self._headers["Proxy-Authorization"] = f"Bearer {self.config.iap_token}"
    else:
        # Kubernetes: Use AirMesh endpoint and service token
        self._host = self.config.host
        self._headers["Authorization"] = f"Bearer {self.config.api_token}"
```

**When to use:**
- Different endpoints for local vs Kubernetes
- IAP authentication for local development
- Service-to-service authentication in Kubernetes

### Retry Logic with Backoff

Use `get_backoff_delay()` for retry operations:

```python
from common.utils.retry_utils import get_backoff_delay

DEFAULT_BACKOFF_DELAYS = [2.0, 4.0, 8.0]
MAX_RETRIES = 3

def _request_with_retry(self, url: str) -> bytes:
    """Make request with exponential backoff retry."""
    last_err: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            delay = get_backoff_delay(attempt - 1, self.backoff_delays)
            logger.warning(
                "Retrying request (attempt %d/%d) after %.1fs",
                attempt + 1,
                MAX_RETRIES + 1,
                delay,
            )
            time.sleep(delay)

        try:
            response = self._make_request(url)

            # Check for retryable status codes
            if self._is_retryable(response.status_code):
                last_err = httpx.HTTPStatusError(...)
                continue

            response.raise_for_status()
            return response.content

        except httpx.HTTPStatusError:
            raise
        except Exception as err:
            last_err = err

    if last_err:
        raise last_err
    raise RuntimeError(f"Request failed after {MAX_RETRIES + 1} attempts")
```

**Retryable conditions:**
- 5xx server errors
- 429 rate limiting
- Network timeouts
- Connection errors

### Datetime Handling

Use datetime utilities for consistent timestamp handling:

```python
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive

# Convert API timestamps to naive UTC
created_at = parse_timestamp_to_utc(api_response.created_at)

# Get current time as naive UTC
updated_at = utc_now_naive()
```

### HTTP Client Usage

Use httpx with context managers:

```python
import httpx

def get_data(self, path: str) -> dict:
    """Fetch data from API."""
    url = f"{self._base_url}/{path}"

    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=self._headers)
        response.raise_for_status()
        return response.json()
```

**Key points:**
- Use context managers for automatic cleanup
- Set explicit timeouts (default: 30s, LLM: 120s)
- Include headers for authentication

---

## Async vs Sync Clients

### When to Provide Both

Provide both async and sync versions when:
- Service supports high-concurrency operations (batch processing)
- Callers may be async (FastAPI) or sync (scripts, jobs)
- Performance benefits from concurrent requests

**Examples:** `FacadeClient`/`FacadeClientSync`, `GHEClient`/`GHEClientSync`

### When Sync-Only is Sufficient

Sync-only is acceptable when:
- Operations are inherently sequential
- Low request volume
- Simple integration requirements

**Examples:** `JiraClient`, `MatikApiClient`, `GreenroomClient`

### Async Client Pattern

```python
import asyncio
from dataclasses import dataclass, field

@dataclass
class MyClientAsync:
    """Async client for high-concurrency operations."""

    config: MyConfig
    _client: AsyncClient = field(init=False)

    def __post_init__(self) -> None:
        self._client = AsyncClient(...)

    async def get_items(self) -> list[Item]:
        """Fetch items concurrently."""
        tasks = [self._fetch_item(id) for id in item_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]
```

### Sync Wrapper Pattern

```python
@dataclass
class MyClientSync:
    """Synchronous wrapper for MyClientAsync."""

    config: MyConfig
    _async_client: MyClientAsync = field(init=False)

    def __post_init__(self) -> None:
        self._async_client = MyClientAsync(config=self.config)

    def get_items(self) -> list[Item]:
        """Sync wrapper for async get_items."""
        return asyncio.get_event_loop().run_until_complete(
            self._async_client.get_items()
        )
```

---

## Client Reference

### FacadeClient (LLM/Facade)

**Purpose:** Interact with Airbnb's LLM Fusion Hub

| Attribute | Description |
|-----------|-------------|
| Config | `FacadeConfig` |
| Auth | IAP token (local) or AirMesh (K8s) |
| Retry | Yes, configurable via config |
| Async | Yes (`FacadeClient`, `FacadeClientSync`) |

**Key methods:**
- `send_message(model, messages)` - Send chat completion request
- `send_message_with_retry(model, messages)` - With automatic retry

**Factory functions:**
- `create_facade_client(...)` - Create async client
- `create_facade_client_sync(...)` - Create sync client

**Helper functions:**
- `facade_system_message(content)` - Create system message
- `facade_user_message(content)` - Create user message
- `facade_assistant_message(content)` - Create assistant message

### GHEClient (GitHub Enterprise)

**Purpose:** Interact with GitHub Enterprise API

| Attribute | Description |
|-----------|-------------|
| Config | `BiztechGitHubConfig` |
| Auth | GitHub App (private key + installation ID) |
| Retry | Yes, with rate limit handling |
| Async | Yes (`GHEClient`, `GHEClientSync`) |
| Hash Cache | `PRHashCache` (for LLM call optimization) |

**Key methods:**
- `get_orgs()` - List organizations
- `get_repos(org)` - List repositories for org
- `get_pull_requests(org, repo)` - List pull requests
- `list_pull_requests_with_hash_cache(...)` - PRs with LLM call optimization

**Enrichment:** GHEClient does **not** call the LLM/Facade. When an
`EnrichmentPublisher` is injected, it publishes an enrichment request per PR to the
Enricher SQS queue; the Enricher owns LLM summarization and hash-based change
detection. (`PRHashCache` still exists for file/service-fetch gating; the crawler
passes an empty one.) See [Facade](facade.md#llm-call-optimization-with-content-hashing).

**Factory functions:**
- `create_ghe_client(...)` - Create async client
- `create_ghe_client_sync(...)` - Create sync client

### GreenroomClient (Backstage)

**Purpose:** Interact with Greenroom/Backstage catalog API

| Attribute | Description |
|-----------|-------------|
| Config | `GreenroomConfig`, `CommonConfig` |
| Auth | IAP + Backstage token (local) or API token (K8s) |
| Retry | No |
| Async | No (sync only) |

**Key methods:**
- `get_entity_by_name(kind, namespace, name)` - Get specific entity
- `get_entity_by_uid(uid)` - Get entity by UID
- `query_entities(filters)` - Query with pagination

**Factory function:** `create_greenroom_client(...)`

### JiraClient

**Purpose:** Interact with JIRA REST API

| Attribute | Description |
|-----------|-------------|
| Config | `JiraConfig` |
| Auth | Basic auth (username/password) |
| Retry | No |
| Async | No (sync only) |
| Enrichment | Publishes to the Enricher via `EnrichmentPublisherSync` (no inline LLM) |

**Key methods:**
- `fetch_issues(jql)` - Query issues with JQL (client-internal pagination)
- `enrich_issues(...)` - Publishes an enrichment request per issue to the Enricher
  (base fields only; summaries are filled asynchronously by the Enricher)

**Enrichment:** JiraClient does **not** call the LLM/Facade. With an
`EnrichmentPublisherSync` injected, `enrich_issues` publishes per-issue enrichment
requests to the Enricher SQS queue; the Enricher owns summarization + hash-based
change detection.

**Legacy hash caching:** `JiraHashCache` - Holds pre-fetched hash data (keyed by issue_key)
- Uses two hashes: `summary_hash` (issue summary) and `comments_hash` (aggregated comments)
- See [LLM Call Optimization with Content Hashing](#llm-call-optimization-with-content-hashing) for pattern details

**Factory function:** `create_jira_client(...)`

### MatikApiClient

**Purpose:** Internal Matik catalog/API service

| Attribute | Description |
|-----------|-------------|
| Config | `ApiConfig` |
| Auth | None (internal service) |
| Retry | Yes, with exponential backoff |
| Async | No (sync only) |

**Key methods:**
- `get_request(path, params)` - GET request
- `post_json_request(path, body)` - POST with JSON body
- `get_request_with_body(path, body)` - GET with body (non-standard)

**Factory function:** `create_matik_api_client(...)`

---

## Error Handling

### Logging Pattern

Log errors with context but don't halt execution:

```python
def get_entity(self, id: str) -> Entity | None:
    """Fetch entity by ID."""
    try:
        response = self._client.get(f"/entities/{id}")
        response.raise_for_status()
        return Entity.model_validate(response.json())
    except httpx.HTTPStatusError as err:
        logger.error("HTTP error fetching entity %s: %s", id, err)
        raise  # Re-raise HTTP errors for caller to handle
    except Exception as err:
        logger.error("Error fetching entity %s: %s", id, err)
        return None  # Return None for other errors
```

### Return Types

| Scenario | Return Type | Example |
|----------|-------------|---------|
| Success | Model/data | `Entity`, `list[Item]`, `bytes` |
| Not found | `None` or empty | `None`, `[]` |
| Error (recoverable) | `None` | Network timeout |
| Error (unrecoverable) | Raise exception | 4xx HTTP errors |

---

## Testing Patterns

### Mock HTTP Client

```python
from unittest.mock import MagicMock, patch
import pytest

class TestMyClient:
    @pytest.fixture
    def mock_config(self) -> MyConfig:
        return MyConfig(base_url="https://api.example.com")

    @pytest.fixture
    def client(self, mock_config: MyConfig) -> MyClient:
        return MyClient(my_config=mock_config)

    def test_get_data_success(self, client: MyClient) -> None:
        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"id": 1, "name": "test"}
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response

            result = client.get_data("/items/1")

            assert result["id"] == 1
```

### Test Coverage Requirements

- Constructor/initialization tests
- Success path for each method
- Error handling (HTTP errors, network errors)
- Retry logic (if applicable)
- Environment-specific behavior (local vs K8s)

---

## Checklist for New Clients

### Client Implementation

- [ ] Use `@dataclass` decorator
- [ ] Accept config objects as constructor parameters
- [ ] Private fields use `field(init=False)`
- [ ] Initialization logic in `__post_init__`
- [ ] Include usage example in class docstring
- [ ] All methods have type hints
- [ ] All methods have docstrings with Args/Returns

### Factory Function

- [ ] Create `create_*_client()` function
- [ ] Accept same config parameters as client
- [ ] Include usage example in docstring

### Error Handling

- [ ] Log errors with context via `logger.error()`
- [ ] Return `None` for recoverable errors
- [ ] Re-raise HTTP status errors for caller handling
- [ ] Include retry logic for transient failures (if needed)

### Environment Handling

- [ ] Use `is_local_environment()` if auth differs by environment
- [ ] Handle IAP token for local development
- [ ] Handle service tokens for Kubernetes

### Module Exports

- [ ] Import client in `common/clients/__init__.py`
- [ ] Import factory function in `__init__.py`
- [ ] Add to `__all__` list in alphabetical order

### Testing

- [ ] Create `*_client_test.py` file
- [ ] Test constructor/initialization
- [ ] Test success paths
- [ ] Test error handling
- [ ] Test retry logic (if applicable)
- [ ] mypy passes with no errors
- [ ] ruff check passes

### LLM Call Optimization (if client generates LLM summaries)

- [ ] Create `*HashInfo` dataclass in DAO for hash/summary storage
- [ ] Create `*HashCache` dataclass in client for in-memory cache
- [ ] Add `*_hash` column(s) to database model (`String(64)`)
- [ ] Add `llm_summary` column(s) to database model (`Text`)
- [ ] Create Alembic migration for new columns
- [ ] Add `get_*_hashes_by_ids()` method to DAO
- [ ] Update DAO upsert methods to include hash/summary fields
- [ ] Create `*_with_hash_cache()` method in client
- [ ] Log cache hit/miss statistics
- [ ] Test cache hit scenarios (hash matches)
- [ ] Test cache miss scenarios (hash mismatch, new records)
- [ ] Export hash cache class in `__init__.py`

---

## Reference Implementations

**Sync client with retry:** `matik/common/clients/matik_api_client.py`

**Async + sync client:** `matik/common/clients/ghe_client.py`

**Environment-aware client:** `matik/common/clients/greenroom_client.py`

**LLM integration client:** `matik/common/clients/jira_client.py`

**Utility functions:**
- Environment detection: `matik/common/utils/env_utils.py`
- Retry backoff: `matik/common/utils/retry_utils.py`
- Datetime handling: `matik/common/utils/datetime_utils.py`
