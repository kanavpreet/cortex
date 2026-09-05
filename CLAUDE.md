# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Matik (from Tagalog "awtomatiko" meaning automate) is an AIOps platform providing unified reliability data catalog and reliability data trained LLMs for powering intelligence and automation in technical operations. The project focuses on Detection (cataloging events), Prevention (reducing MTTD/MTTM/MTTR), and Resolution (automatically triaging/resolving alerts and tickets).

**Note:** The project is being migrated from Go to Python. New development should use the Python codebase in `matik/` directory.

## Pull Requests

- **Every PR description MUST include a "Test Plan" section.** The CI pipeline fails if it is missing. Add a `## Test Plan` heading describing how the change was verified (tests run, manual checks, etc.) whenever creating a PR.

## Development Commands

### Python (New - Primary)

```bash
# Navigate to Python project
cd matik/

# Install dependencies (using uv package manager)
uv sync

# Run tests
uv run pytest
uv run pytest -v                      # Verbose output
uv run pytest --cov=matik             # With coverage

# Code quality
uv run ruff check .                   # Lint
uv run ruff check --fix .             # Lint with auto-fix
uv run ruff format .                  # Format code
uv run mypy .                         # Type checking

# Run specific services
uv run python -m matik.historian
uv run python -m matik.chronicler
uv run python -m matik.api
uv run python -m matik.migrator
```

### Go (Legacy)

```bash
# Build and test
go build ./...
go test ./...
go test -v ./...

# Code quality
go fmt ./...
go vet ./...
go mod tidy

# Run specific services
go run ./historian
go run ./chronicler
go run ./catalog
go run ./correlator

# Build binaries
go build -o historian ./historian
go build -o chronicler ./chronicler
go build -o catalog ./catalog
go build -o correlator ./correlator
```

## Architecture

### Python Architecture (New - Primary)

Multi-service Python application in the `matik/` directory with shared packages in `matik/common/`:

- **matik/common/models/**: Pydantic domain models for validation and serialization (`incidentio.py`)
- **matik/common/clients/**: External API client wrappers (async, using httpx or SDK libraries)
- **matik/common/daos/**: Database operations using SQLAlchemy Core with async support
- **matik/common/utils/**: Shared utilities including database connection management
- **matik/common/migrations/**: Alembic migration files for schema versioning
- **matik/common/queues/**: Message queue infrastructure with async SQS integration
- **matik/historian/**: Service that catalogs past events (incidents from Incident.io, PagerDuty)
- **matik/chronicler/**: Service with state machines that catalogs events as they happen via webhooks
- **matik/api/**: FastAPI service for LLM-powered analysis and summaries
- **matik/migrator/**: Service for database migrations and data migration tasks

### Go Architecture (Legacy)

Multi-service Go application with shared internal packages in the `common/` directory:

- **common/models/**: Domain models with JSON tags for serialization (`incidentio_incident.go`, `greenroom_entity.go`, `db_connection.go`, `connector_types.go`)
- **common/clients/**: External API client wrappers with data transformation (Incident.io, PagerDuty, Greenroom/Backstage, AWS services, Enigmatologist)
- **common/greenroom/**: OpenAPI-generated Greenroom/Backstage API client library (internal package)
- **common/daos/**: Database operations using Squirrel query builder pattern
- **common/utils/**: Shared utilities including database connection management
- **common/migrations/**: SQL migration files for schema versioning
- **common/queues/**: Message queue infrastructure with SQS integration and processor patterns
- **historian/**: Service that catalogs past events (incidents from Incident.io, PagerDuty)
- **chronicler/**: Service with state machines that catalogs events as they happen via webhooks
- **catalog/**: Service for LLM-powered analysis and summaries
- **correlator/**: Service for dispatching historical data collection tasks

## Data Sources

The platform integrates multiple data sources with assigned ownership:

- **incident.io** (incidents) - In progress (@julie-trias)
- **OpsBot / generic Matik webhook** (on-demand incident channel summaries) - @alfredo-moreira
- **PagerDuty** (alerts) - @camille-bustamante
- **Google Docs** (post-mortems) - @elham-saboori
- **JIRA** (TCMRs, ops tickets) - @alfredo-moreira
- **AWS Status** (AWS incidents) - @sarang-gosavi
- **Greenroom/Backstage** (catalog entities, service metadata) - Service registry integration
- **Infrastructure**: Terraform, CloudTrail, Cloud Audit Logs
- **Monitoring**: Grafana metrics, Opensearch/Kibana error logs
- **Development**: Git repos, artifactory build events, deployment systems (Jenkins, Argo CD, Spinnaker)

## Key Dependencies & Patterns

### Python Dependencies (New - Primary)

- `pydantic>=2.10`: Data validation and settings management using Python type annotations
- `fastapi`: Modern, fast web framework for building APIs
- `sqlalchemy[asyncio]`: Async SQL toolkit and ORM
- `httpx`: Async HTTP client for external API calls
- `aiobotocore`: Async AWS SDK for SQS and Secrets Manager
- `pytest`: Testing framework with async support
- `ruff`: Fast Python linter and formatter
- `mypy`: Static type checking

### Go Dependencies (Legacy)

- `github.com/andygrunwald/go-incident`: Incident.io API client
- `github.com/andygrunwald/go-jira`: JIRA API client
- `github.com/Masterminds/squirrel`: Type-safe SQL query builder for DAO operations
- `github.com/aws/aws-sdk-go-v2`: AWS SDK v2 with SQS and Secrets Manager support
- `github.com/openai/openai-go/v3`: OpenAI SDK used to interact with Airbnb's LLM Fusion Hub (Facade)
- `github.com/stretchr/testify`: Testing framework
- `github.com/DATA-DOG/go-sqlmock`: SQL mocking for tests

### Key Patterns

- Client layer transforms external API data to internal models
- DAO pattern with upsert operations for data consistency
- Rich domain models with comprehensive field documentation
- Complex incident data modeling with nullable timestamps, arrays, custom fields, and LLM-generated summaries
- Internal package architecture (import as `matik.common.models`, `matik.common.daos`, etc.)
- Message queue processing with SQS for asynchronous data ingestion
- State machine architecture in chronicler for real-time event processing
- LLM integration via API service and Facade client for generating summaries and analysis
- Service mesh configuration with AirMesh for service-to-service communication (`_infra/mesh.yml`)
- **Standardized timestamp handling** with UTC RFC3339 format throughout the application

## Python Model Guidelines

When creating or modifying Pydantic models, follow these conventions:

### Model Structure

```python
"""Module docstring describing the data models."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ExampleModel(BaseModel):
    """
    Class docstring describing the model purpose.

    All timestamps are stored as naive UTC.
    """

    model_config = {"from_attributes": True}  # Enables ORM mode

    # Required fields with descriptions
    id: str = Field(..., description="Unique identifier")

    # Optional fields with defaults
    name: str | None = Field(default=None, description="Optional name")

    # Timestamps - non-nullable
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")

    # Timestamps - nullable
    updated_at: datetime | None = Field(default=None, description="Last update (UTC)")

    # Lists - nullable
    tags: list[str] | None = Field(default=None, description="Optional tags")
```

### Naming Conventions

| Go | Python |
|---|---|
| `IncidentId` | `incident_id` |
| `CreatedAt` | `created_at` |
| `AffectedServices` | `affected_services` |
| `*time.Time` | `datetime \| None` |
| `*[]string` | `list[str] \| None` |
| `*bool` | `bool \| None` |

### Field Validators

Use `@field_validator` for custom parsing logic, especially for timestamps:

```python
from datetime import UTC, datetime

@field_validator("created_at", "updated_at", mode="before")
@classmethod
def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
    """Parse and normalize timestamps to naive UTC."""
    if v is None:
        return None

    # Convert string to datetime
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))

    # If timezone-aware, convert to UTC explicitly before stripping timezone
    if v.tzinfo is not None:
        v = v.astimezone(UTC).replace(tzinfo=None)

    return v
```

**Critical**: Always use `UTC` (Python 3.11+) explicitly when converting timezone-aware datetimes to UTC. Using `.astimezone()` without arguments converts to local server time, not UTC.

### Code Style

- Use `ruff` for linting and formatting (line length: 88, double quotes)
- Use `mypy` with strict settings for type checking
- Follow PEP 8 naming conventions (snake_case for variables/functions)
- Use type annotations for all function signatures
- Import order: stdlib → third-party → local (enforced by ruff/isort)

### Enum Fields - Use Plain Strings

**Do NOT use `Literal` types** for enum-like fields (status, severity, state, etc.) in Pydantic models:

```python
# ✓ CORRECT - Use plain str for flexibility
status: str = Field(..., description="Incident status")
severity: str | None = Field(default=None, description="Severity level")

# ✗ AVOID - Literal types break on API changes
status: Literal["open", "closed"] = Field(...)
```

**Rationale:**
- External API responses may change or add new enum values
- Literal validation failures would cause data ingestion to fail
- Values are written directly to the database without processing
- Plain `str` provides necessary flexibility for evolving APIs

## Timestamp Handling

All timestamps in the application are standardized to UTC timezone. MySQL handles `time.Time` directly, so no string conversion is needed for database operations.

### Utility Functions (common/utils/utils.go)

Use these standardized functions for all timestamp operations:

```go
// For non-nullable timestamps
utils.ToUTC(t time.Time) time.Time                 // Convert to UTC for database writes
utils.ParseTimestamp(s string) (time.Time, error)  // Parse from external API strings
utils.NowUTC() time.Time                            // Get current time in UTC

// For nullable timestamps
utils.ToUTCOrNil(t *time.Time) interface{}                 // Convert to UTC or nil for database (returns time.Time or nil)
utils.ParseNullableTimestamp(s string) (*time.Time, error) // Parse nullable from external API strings

// Standard format constant (for external APIs)
utils.TimestampFormat // time.RFC3339
```

### DAO Layer Pattern

**Writing timestamps to database:**
```go
// Non-nullable timestamps - MySQL handles time.Time directly
Values(
    utils.ToUTC(incident.CreatedAt),
    utils.ToUTC(incident.ReportedAt),
)

// Nullable timestamps - MySQL driver accepts time.Time value or nil (NOT *time.Time)
Values(
    utils.ToUTCOrNil(incident.AcceptedAt),
    utils.ToUTCOrNil(incident.ClosedAt),
)
```

**Reading timestamps from database:**
```go
// Scan directly to time.Time, then convert to UTC
err = db.QueryRow(query, args...).Scan(&incident.CreatedAt, &incident.AcceptedAt)

// Ensure UTC after scanning
incident.CreatedAt = incident.CreatedAt.UTC()
if incident.AcceptedAt != nil {
    utcTime := incident.AcceptedAt.UTC()
    incident.AcceptedAt = &utcTime
}
```

**For string-based timestamps from external APIs (e.g., PagerDuty):**
```go
// Validate and normalize before storing
func normalizeTimestampString(ts *string) *time.Time {
    if ts == nil || *ts == "" {
        return nil
    }
    parsed, err := utils.ParseTimestamp(*ts)
    if err != nil {
        // Log warning and return nil to avoid data corruption
        return nil
    }
    return &parsed  // Already in UTC from ParseTimestamp
}
```

### Model Design

Domain models should use `time.Time` or `*time.Time` for timestamps:

```go
type IncidentIOIncident struct {
    CreatedAt    time.Time   // Non-nullable timestamp
    ReportedAt   time.Time   // Non-nullable timestamp
    AcceptedAt   *time.Time  // Nullable timestamp
    ClosedAt     *time.Time  // Nullable timestamp
}
```

Avoid using `string` types for timestamps unless required by external APIs. If using strings, always validate and normalize them before database operations.

### Important Notes

- **Always use UTC**: All timestamps must be converted to UTC before storage
- **Consistent format**: Use RFC3339 format for all timestamp strings
- **Database storage**: MySQL DATETIME columns store timestamps without timezone info, so UTC enforcement at application layer is critical
- **External APIs**: Convert timestamps from external APIs (Incident.io, PagerDuty, JIRA, GitHub) to UTC immediately upon receipt
- **Migration 000007**: Documents the timestamp standardization implementation

## Architecture Reference

[C4 Architecture Diagram](https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712/edit?viewport_loc=1306%2C964%2C2384%2C2972%2CyJO_wFNQ_qPY&invitationId=inv_16bf17b0-a22f-4cdf-b908-9e90f06fc37c)

## Environment Variables

Required:

- `INCIDENTIO_API_KEY` for Incident.io authentication

**Jira Integration:**

- `JIRA_BASE_URL` for JIRA base URL, it is the Jira Rest endpoint
- `JIRA_USERNAME` for JIRA authentication (usually service account ldap)
- `JIRA_PASSWORD` for JIRA authentication (usually service account password)
- `JIRA_PAGINATION_MAX_RESULTS` for JIRA pagination - OPTIONAL (default: 200)
- `JIRA_HISTORIAN_OPERATIONAL_TICKETS_JQL` for JIRA JQL query to fetch operational tickets
- `JIRA_HISTORIAN_TCMR_TICKETS_JQL` for JIRA JQL query to fetch TCMR tickets

**Greenroom/Backstage Integration:**

- `GREENROOM_HOST` for Greenroom API host - OPTIONAL (default: http://greenroom-production.greenroom-production:7007)
- `GREENROOM_API_TOKEN` for Greenroom API static Bearer token authentication (service-to-service via AirMesh)
