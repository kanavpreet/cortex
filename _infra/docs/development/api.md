# API Guide

## Calling the API

Reference documentation for calling the Matik API.

### Base URL

| Environment | URL |
|-------------|-----|
| Local | `http://localhost:8080` |
| Kubernetes | `https://api-matik-{environment}.a.musta.ch/` |

### Request Headers

#### X-Task-ID (Recommended)

Include the `X-Task-ID` header for distributed tracing and log correlation.

```
X-Task-ID: <unique-identifier>
```

**Note:** The header name is defined in `common.constants.TASK_ID_HEADER`.

**Behavior:**
- If provided, the API uses your task ID for all logs related to the request
- If omitted, the API generates a task ID with format `{service}-{uuid}` (e.g., `api-550e8400-e29b-41d4-a716-446655440000`)
- The response always includes the `X-Task-ID` header (echoed or generated)

**Example:**
```bash
curl -H "X-Task-ID: historian-abc123-def456" http://localhost:8080/v1/ghe/organizations
```

**Use cases:**
- Trace requests across multiple services
- Correlate client logs with API logs
- Debug issues by searching logs with the task ID

#### Content-Type

For POST/PUT requests with JSON body:

```
Content-Type: application/json
```

### OpenAPI Documentation

Interactive API documentation is available at:

- **Swagger UI:** `http://localhost:8080/docs`
- **ReDoc:** `http://localhost:8080/redoc`
- **OpenAPI JSON:** `http://localhost:8080/openapi.json`

---

## Adding New Routes

Guide for adding new routes to the FastAPI service.

### Project Structure

```
matik/api/
├── main.py              # App entry point, router registration
├── routes/
│   ├── __init__.py      # Export all routers
│   ├── deps.py          # Dependency injection (DAOs, engine)
│   ├── health.py        # Health check routes
│   ├── ghe_*.py         # GitHub Enterprise routes
│   ├── jira.py          # JIRA routes
│   └── *_test.py        # Tests for each route module
```

### Adding a New Route

#### 1. Create the Route File

```python
# api/routes/my_feature.py
"""My feature endpoints."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.routes.deps import MyDAODep
from common.models import MyModel
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

router = APIRouter(prefix="/v1/my-feature", tags=["my-feature"])


class MyRequest(BaseModel):
    """Request body for creating a resource."""
    name: str
    value: int


class MyResponse(BaseModel):
    """Response body."""
    id: int
    name: str


@router.post("/", status_code=status.HTTP_200_OK)
def create_resource(
    request: MyRequest,
    dao: MyDAODep,
) -> MyResponse:
    """Create a new resource."""
    result = dao.insert(request)
    if result is None:
        logger.error(f"Failed to create resource: {request.name}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create resource",
        )
    return MyResponse(id=result, name=request.name)
```

#### 2. Add DAO Dependency

```python
# api/routes/deps.py

from common.daos import MyDAO

def get_my_dao(engine: Annotated[Engine, Depends(get_engine)]) -> MyDAO:
    """Get My DAO."""
    return MyDAO(engine)

MyDAODep = Annotated[MyDAO, Depends(get_my_dao)]
```

#### 3. Register the Router

```python
# api/routes/__init__.py
from api.routes.my_feature import router as my_feature_router

__all__ = [
    # ... existing routers
    "my_feature_router",
]
```

```python
# api/main.py
from api.routes import my_feature_router

app.include_router(my_feature_router)
```

#### 4. Write Tests

```python
# api/routes/my_feature_test.py
"""Tests for my feature endpoints."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.deps import get_my_dao
from api.routes.my_feature import router


@pytest.fixture
def app() -> FastAPI:
    """Create test app with router."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_dao() -> MagicMock:
    """Create mock DAO."""
    return MagicMock()


class TestCreateResource:
    """Tests for POST endpoint."""

    def test_creates_successfully(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.insert.return_value = 1
        app.dependency_overrides[get_my_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.post("/v1/my-feature/", json={"name": "test", "value": 42})

        assert response.status_code == 200
        assert response.json()["id"] == 1

    def test_returns_500_on_failure(
        self, app: FastAPI, mock_dao: MagicMock
    ) -> None:
        mock_dao.insert.return_value = None
        app.dependency_overrides[get_my_dao] = lambda: mock_dao
        client = TestClient(app)

        response = client.post("/v1/my-feature/", json={"name": "test", "value": 42})

        assert response.status_code == 500
```

### Best Practices

#### Request/Response Models

- Use Pydantic `BaseModel` for request/response schemas
- Keep models in the route file unless shared across routes
- Use `Field()` for descriptions and validation

```python
from pydantic import BaseModel, Field

class CreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Resource name")
    count: int = Field(default=0, ge=0, description="Count must be non-negative")
```

#### Error Handling

- Use `HTTPException` for all error responses
- Log errors before raising exceptions
- Use appropriate status codes:

| Code | When to Use |
|------|-------------|
| 400 | Invalid input (validation handled by Pydantic returns 422) |
| 404 | Resource not found |
| 500 | DAO/database failures |

```python
if not dao.exists(id):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

result = dao.insert(data)
if result is None:
    logger.error(f"Insert failed for {data}")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create")
```

#### Path Parameters

```python
@router.get("/{resource_id}")
def get_resource(resource_id: int, dao: MyDAODep) -> MyModel:
    result = dao.find(resource_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Not found")
    return result
```

#### Query Parameters

```python
@router.get("/")
def list_resources(
    limit: int = 100,
    offset: int = 0,
    dao: MyDAODep,
) -> list[MyModel]:
    return dao.list(limit=limit, offset=offset)
```

#### Batch Operations

For bulk inserts, return affected row count:

```python
class BatchRequest(BaseModel):
    items: list[MyModel]

class BatchResponse(BaseModel):
    affected_rows: int

@router.post("/batch")
def batch_create(request: BatchRequest, dao: MyDAODep) -> BatchResponse:
    if not request.items:
        return BatchResponse(affected_rows=0)

    affected = dao.batch_insert(request.items)
    if affected is None:
        raise HTTPException(status_code=500, detail="Batch insert failed")
    return BatchResponse(affected_rows=affected)
```
