# Data Access Objects (DAOs) Design Guide

## Overview

Data Access Objects (DAOs) provide a clean abstraction layer for database operations in the Matik platform. This guide documents the patterns established during the migration from Go to Python.

### Technology Stack

- **SQLModel** for defining table schemas with Pydantic validation
- **SQLAlchemy Core** (not ORM) for explicit SQL control with type safety
- **Pydantic models** for data validation and serialization
- **MySQL** as the database engine
- **Location:** `matik/common/daos/`

### Why SQLModel + SQLAlchemy Core?

We use SQLModel for models and SQLAlchemy Core for DAOs to:
- Define models once with SQLModel (`table=True`) that serve both as table schema and Pydantic validators
- Access underlying SQLAlchemy table via `Model.__table__` for DAO operations
- Maintain explicit SQL control with type safety via SQLAlchemy Core
- Derive the batch upsert generically from the model + `DataSourceSpec` (via the
  shared `BaseUpsertDAO`) instead of hand-writing per-source SQL
- Avoid implicit session management and lazy loading from SQLAlchemy ORM
- Optimize performance with direct query control
- Have full control over transactions

## File Structure

> The single-table snippets below use an illustrative `ghe_organization` /
> `GHEOrganization` entity to show the plain-CRUD + soft-delete pattern. It is a
> teaching example, not a current table (GHE data lives in `ghe_pull_requests`).
> For real single-table DAOs to copy from, see `ghe_org_crawl_tracker_dao.py` or the
> tracker DAOs under `common/daos/`. Bulk data sources should subclass
> `BaseUpsertDAO` — see [Batch Operations](#batch-operations--baseupsertdao).

### Simple DAO (One Table)

For resources with a single table:

```
matik/common/daos/
├── __init__.py                    # Module exports
├── ghe_organization_dao.py        # DAO implementation
└── ghe_organization_dao_test.py   # Unit tests
```

### Resource + Tracker (Two Tables)

For resources that need incremental crawling tracking:

```
matik/common/daos/
├── __init__.py                    # Module exports
├── ghe_pr_dao.py                  # PR DAO with batch operations
├── ghe_pr_dao_test.py             # PR tests
├── ghe_pr_tracker_dao.py          # Tracker DAO (separate file)
└── ghe_pr_tracker_dao_test.py     # Tracker tests
```

**Principle:** One DAO per table

## Module Exports (`__init__.py`)

All DAOs must be exported from `common/daos/__init__.py` to provide a clean public API.

### The Pattern

```python
"""Data Access Objects for database operations."""

from common.daos.ghe_organization_dao import GHEOrganizationDAO
from common.daos.ghe_pr_dao import GHEPRDAO, PRHashInfo
from common.daos.ghe_pr_tracker_dao import GHEPRTrackerDAO

__all__ = [
    "GHEPRDAO",
    "GHEOrganizationDAO",
    "GHEPRTrackerDAO",
    "PRHashInfo",
]
```

### Why This Is Required

1. **Cleaner imports** - Users can write:
   ```python
   from common.daos import GHEOrganizationDAO
   ```
   Instead of the verbose:
   ```python
   from common.daos.ghe_organization_dao import GHEOrganizationDAO
   ```

2. **Public API definition** - The `__all__` list explicitly defines what the package exposes to consumers. Internal helper classes or functions not in `__all__` are considered private implementation details.

3. **IDE support** - Exporting classes at package level provides better autocomplete and import suggestions in IDEs like VS Code and PyCharm.

4. **Consistency** - This mirrors the pattern used in `common/models/__init__.py` for SQLModel classes, maintaining a uniform import style across the codebase.

5. **Prevents circular imports** - By centralizing exports, you avoid scattered imports that can lead to circular dependency issues.

### When Adding New DAOs

When creating a new DAO:

1. Import the DAO class in `common/daos/__init__.py`:
   ```python
   from common.daos.my_new_dao import MyNewDAO
   ```

2. Add the class name to `__all__` in alphabetical order:
   ```python
   __all__ = [
       "GHEPRDAO",
       "GHEOrganizationDAO",
       "GHEPRTrackerDAO",
       "MyNewDAO",  # Add new DAOs alphabetically
       "PRHashInfo",
   ]
   ```

## Implementation Patterns

### Table Metadata Definition

**Current Approach:** Define models using SQLModel with `table=True`, then access the underlying SQLAlchemy table in DAOs.

#### Step 1: Define SQLModel in `common/models/`

```python
"""GHE organization data model using SQLModel."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, String
from sqlmodel import Field, SQLModel


class GHEOrganization(SQLModel, table=True):
    """Represents a GitHub Enterprise organization.

    Maps to: ghe_organizations table
    """

    __tablename__ = "ghe_organizations"
    __table_args__ = {"extend_existing": True}

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    # Organization name
    org: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True),
        description="GitHub organization name",
    )

    # Organization ID from GitHub API
    org_id: int = Field(
        sa_column=Column(BigInteger, nullable=False, unique=True),
        description="GitHub organization ID",
    )

    # Soft delete timestamp
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="Soft delete timestamp",
    )
```

#### Step 2: Access SQLAlchemy Table in DAO

```python
"""Data Access Object for GitHub Enterprise organizations table."""

import logging
from sqlalchemy import Engine

from common.models.ghe_organization import GHEOrganization

logger = logging.getLogger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
ghe_organizations_table = GHEOrganization.__table__  # type: ignore[attr-defined]


class GHEOrganizationDAO:
    """DAO for ghe_organizations table with soft delete support."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # Use ghe_organizations_table for SQLAlchemy Core operations
    # ...
```

**Benefits:**
- **Single source of truth**: Model definition includes both table schema and Pydantic validation
- **Less boilerplate**: No need to manually define Table objects in DAOs
- **Type safety**: SQLModel provides full type hints for model fields
- **Validation**: Automatic Pydantic validation when creating model instances
- **Migration alignment**: SQLModel schema matches migration files

### Class Structure

```python
import logging
from sqlalchemy import Engine

logger = logging.getLogger(__name__)

class GHEOrganizationDAO:
    """DAO for ghe_organizations table with soft delete support."""

    def __init__(self, engine: Engine) -> None:
        """
        Initialize the DAO with a SQLAlchemy engine.

        Args:
            engine: SQLAlchemy engine for database operations
        """
        self._engine = engine

    # Standard CRUD methods
    def find_ghe_org(self, org_id: int) -> GHEOrganization | None:
        """Find by natural key."""

    def insert_new_org(self, org: GHEOrganization) -> int | None:
        """Insert new record."""

    def update_org(self, org: GHEOrganization) -> int | None:
        """Update existing record."""

    def delete_org(self, org_id: int) -> bool:
        """Soft delete record."""

    def insert_or_update_ghe_org(self, org: GHEOrganization) -> int | None:
        """Upsert: insert if not exists, update if exists."""
```

### Error Handling

**Key Pattern:** Pythonic - catch exceptions internally, log errors, return `None`/`False` on failure.

**Principles:**
- All methods catch `Exception` internally
- Log errors via `logger.error()` with context
- Return `None` for find/insert/update failures
- Return `False` for delete failures
- Program never halts - callers check return values and handle gracefully

**Example:**

```python
def find_ghe_org(self, org_id: int) -> GHEOrganization | None:
    """
    Find a GHE organization by GitHub org ID.

    Args:
        org_id: GitHub organization ID

    Returns:
        GHEOrganization if found, None if not found or on error
    """
    try:
        stmt = select(ghe_organizations_table).where(
            ghe_organizations_table.c.org_id == org_id
        )
        with self._engine.connect() as conn:
            result = conn.execute(stmt).fetchone()

        if result is None:
            logger.debug(f"No GHE organization found with org_id={org_id}")
            return None

        return GHEOrganization.model_validate(result._mapping)
    except Exception as e:
        logger.error(f"Error finding GHE org with org_id={org_id}: {e}")
        return None
```

**Caller Handling:**

```python
# Not found returns None (not an error)
org = dao.find_ghe_org(999)
if org is None:
    logger.info("Organization not found, creating new one")

# Database errors caught internally, logged, and None returned
org_id = dao.insert_new_org(org)
if org_id is None:
    logger.warning("Failed to insert org, continuing without it")
    # Continue execution - don't halt
else:
    logger.info(f"Organization created with id={org_id}")
```

### Return Types

| Operation | Return Type | Success | Failure |
|-----------|-------------|---------|---------|
| find | `Model \| None` | Model instance | `None` |
| insert | `int \| None` | New auto-increment ID | `None` |
| update | `int \| None` | Record ID | `None` |
| delete | `bool` | `True` | `False` |
| upsert | `int \| None` | Record ID | `None` |

### Connection Management

Use context managers for automatic connection cleanup:

```python
def insert_new_org(self, org: GHEOrganization) -> int | None:
    try:
        stmt = insert(ghe_organizations_table).values(
            org=org.org,
            org_id=org.org_id,
        )
        with self._engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()  # Explicit commit required

        logger.info(f"Inserted GHE org '{org.org}' with id={result.lastrowid}")
        return result.lastrowid
    except Exception as e:
        logger.error(f"Error inserting GHE org '{org.org}': {e}")
        return None
# Connection auto-closed even if exception occurs
```

### Soft Delete Semantics

**Pattern:** Set timestamp, never physically delete rows.

- **Delete operation**: Sets `deleted_at` to current UTC timestamp
- **Update operation**: Resets `deleted_at` to `None` (re-discovery pattern)
- **Find operation**: Returns even soft-deleted records (caller decides how to handle)

```python
from datetime import UTC, datetime

def delete_org(self, org_id: int) -> bool:
    """Soft delete by setting deleted_at timestamp."""
    try:
        stmt = (
            update(ghe_organizations_table)
            .where(ghe_organizations_table.c.org_id == org_id)
            .values(deleted_at=datetime.now(UTC).replace(tzinfo=None))
        )
        with self._engine.connect() as conn:
            conn.execute(stmt)
            conn.commit()
        logger.info(f"Soft deleted GHE org with org_id={org_id}")
        return True
    except Exception as e:
        logger.error(f"Error soft deleting GHE org with org_id={org_id}: {e}")
        return False

def update_org(self, org: GHEOrganization) -> int | None:
    """Update resets deleted_at to NULL (re-discovery)."""
    try:
        stmt = (
            update(ghe_organizations_table)
            .where(ghe_organizations_table.c.id == org.id)
            .values(
                org=org.org,
                org_id=org.org_id,
                deleted_at=None,  # Reset soft delete on update
            )
        )
        with self._engine.connect() as conn:
            conn.execute(stmt)
            conn.commit()
        logger.info(f"Updated GHE org '{org.org}' with id={org.id}")
        return org.id
    except Exception as e:
        logger.error(f"Error updating GHE org '{org.org}': {e}")
        return None
```

## Batch Operations — `BaseUpsertDAO`

For data sources that ingest large volumes (incidentio, GHE PR, JIRA), the
batch-upsert and LLM-partial-update logic is **inherited, not hand-written**. Since
the "paved path" refactor there is a single shared base, `BaseUpsertDAO`
(`common/daos/base_dao.py`), and each source DAO subclasses it. There is **no**
per-source `INSERT ... ON DUPLICATE KEY UPDATE` SQL, no query builder, and no
hand-maintained column list — the upsert is derived generically from the source's
`DataSourceSpec` and its record model.

### When to Use Batch Operations

- Ingesting hundreds or thousands of records at once (historical backfill)
- High-throughput ingestion via Scribe
- Examples: GHE pull requests, incidents, JIRA tickets

### How `BaseUpsertDAO` works

`BaseUpsertDAO.__init__(spec, engine, metrics)` stores the source's
`DataSourceSpec` and `spec.record_model.__table__`. From that it provides:

- **`upsert_batch(models, dropped_columns=())`** — chunks by `BATCH_SIZE = 500`,
  builds each row generically with `_derived_row` (`model.model_dump()` minus the
  DB-managed columns), and issues a native SQLAlchemy MySQL upsert:

  ```python
  from sqlalchemy.dialects.mysql import insert as mysql_insert

  stmt = mysql_insert(table).values(values)
  stmt = stmt.on_duplicate_key_update(
      {col: stmt.inserted[col] for col in upsert_columns}
  )
  ```

  `upsert_columns` is the spec's derived `update_columns` — every table column
  except the conflict keys, the DB-managed audit columns, the LLM columns, and
  anything in `spec.exclude_columns`. So a base write never clobbers async LLM
  summaries, and columns are never listed by hand.
- **`update_llm_fields_from_message(**values)`** — the enrichment partial update:
  pops `spec.enrichment_key`, writes the remaining (LLM) columns via
  `execute_with_retry`, returning the tri-state `True` / `False` (row not found) /
  `None` (error).

All writes route through `execute_with_retry` (transient-MySQL retry on a fresh
connection) and the `DBMetrics.start_query` timing idiom.

### What a source DAO writes

A concrete DAO subclasses `BaseUpsertDAO`, wires the spec in `__init__`, and adds
**only its finders** — the batch upsert and LLM update come for free:

```python
class IncidentIOIncidentDAO(BaseUpsertDAO):
    def __init__(self, engine, metrics=None, source_type="incidentio"):
        # Lazy import avoids the spec <-> DAO import cycle.
        from common.datasources.registry import get_source
        super().__init__(get_source(source_type), engine, metrics)

    def find_incident_by_id(self, incident_id: str) -> IncidentIOIncident | None:
        ...  # source-specific lookups only
```

The spec's `dao_factory` (`(engine, metrics) -> BaseUpsertDAO`) is how Scribe and the
historian build the DAO. A public `upsert_<source>_batch(...)` alias may delegate to
`self.upsert_batch(...)` for a source-friendly name (e.g.
`IncidentIOIncidentDAO.upsert_incidents_batch`).

### Key Points

- **BATCH_SIZE = 500**: stays within MySQL `max_allowed_packet`.
- **Unique index / conflict keys**: the table's `UniqueConstraint` must match the
  spec's `conflict_keys`; that's what `ON DUPLICATE KEY UPDATE` fires on.
- **JSON list columns pass through natively** — `_derived_row` hands `list[str]`
  straight to the `Column(JSON)` type. Never `json.dumps` (double-encodes).
- **LLM columns are never clobbered** by a base upsert — they're excluded from
  `update_columns` and only written by `update_llm_fields_from_message`.
- **Re-discovery / immutable columns**: use `spec.exclude_columns` (denylist) or the
  `dropped_columns` arg to keep a column out of the conflict UPDATE set.
- See [Onboarding a Data Source](onboarding-a-data-source.md) for the full
  spec-and-DAO wiring, and `common/datasources/registry.py` for the `DataSourceSpec`
  contract (`conflict_keys`, `update_columns`, `llm_columns`).

## Tracker Operations

For data sources that need incremental crawling, create a separate tracker DAO in its own file.

### When to Use Trackers

Create a tracker DAO when:
- Resource has incremental crawling (cutoff date tracking)
- Need to track last successful crawl timestamp
- Want to avoid reprocessing old data
- Examples: `ghe_pr_tracker`, `incidentio_tracker`, `jira_batch_tracker`

### File Organization

**Principle:** One DAO per table

```
matik/common/daos/
├── ghe_pr_dao.py              # PR CRUD + batch operations
└── ghe_pr_tracker_dao.py      # Tracker operations (separate file)
```

### Tracker DAO Structure

```python
from datetime import datetime
from sqlalchemy import Engine, select, insert, update

class GHEPRTrackerDAO:
    """DAO for ghe_pr_tracker table - tracks incremental crawling progress."""

    def __init__(self, engine: Engine) -> None:
        """
        Initialize the tracker DAO.

        Args:
            engine: SQLAlchemy engine for database operations
        """
        self._engine = engine

    def get_tracker_cutoff(self, org_id: int, repo_id: int) -> datetime | None:
        """Get cutoff date for incremental crawling."""

    def upsert_tracker(self, tracker: PRTracker) -> bool:
        """Insert or update tracker record."""

    def get_all_trackers(self) -> list[PRTracker] | None:
        """Get all tracker records for monitoring."""
```

### Tracker Implementation Example

```python
"""Data Access Object for GHE PR tracker table."""

import logging
from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select, update

from common.models.ghe_pr_tracker import GHEPRTracker

logger = logging.getLogger(__name__)

# Get table from SQLModel class
# Note: __table__ is dynamically created by SQLModel when table=True
ghe_pr_tracker_table = GHEPRTracker.__table__  # type: ignore[attr-defined]


class GHEPRTrackerDAO:
    """DAO for ghe_pr_tracker table - tracks incremental crawling progress."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

def get_tracker_cutoff(self, org_id: int, repo_id: int) -> datetime | None:
    """
    Get cutoff date for incremental crawling.

    Args:
        org_id: GitHub organization ID
        repo_id: Repository ID

    Returns:
        Cutoff datetime if tracker exists, None if no tracker or error
    """
    try:
        stmt = select(ghe_pr_tracker_table.c.cutoff_date).where(
            ghe_pr_tracker_table.c.org_id == org_id,
            ghe_pr_tracker_table.c.repo_id == repo_id,
        )
        with self._engine.connect() as conn:
            result = conn.execute(stmt).fetchone()

        if result is None:
            logger.debug(f"No tracker found for org_id={org_id}, repo_id={repo_id}")
            return None

        return result[0]  # Return cutoff_date directly
    except Exception as e:
        logger.error(f"Error getting tracker cutoff: {e}")
        return None

def upsert_tracker(self, tracker: PRTracker) -> bool:
    """
    Insert or update tracker record.

    Args:
        tracker: Tracker model with cutoff date and counts

    Returns:
        True on success, False on error
    """
    existing = self.get_tracker_cutoff(tracker.org_id, tracker.repo_id)

    if existing is None:
        return self._insert_tracker(tracker)
    return self._update_tracker(tracker)

def _insert_tracker(self, tracker: PRTracker) -> bool:
    """Private: Insert new tracker record."""
    try:
        stmt = insert(ghe_pr_tracker_table).values(
            org_id=tracker.org_id,
            repo_id=tracker.repo_id,
            cutoff_date=tracker.cutoff_date,
            prs_crawled_count=tracker.prs_crawled_count,
        )
        with self._engine.connect() as conn:
            conn.execute(stmt)
            conn.commit()
        logger.info(f"Created tracker for org={tracker.org_id}, repo={tracker.repo_id}")
        return True
    except Exception as e:
        logger.error(f"Error inserting tracker: {e}")
        return False

def _update_tracker(self, tracker: PRTracker) -> bool:
    """Private: Update existing tracker record."""
    try:
        stmt = (
            update(ghe_pr_tracker_table)
            .where(
                ghe_pr_tracker_table.c.org_id == tracker.org_id,
                ghe_pr_tracker_table.c.repo_id == tracker.repo_id,
            )
            .values(
                cutoff_date=tracker.cutoff_date,
                prs_crawled_count=tracker.prs_crawled_count,
            )
        )
        with self._engine.connect() as conn:
            conn.execute(stmt)
            conn.commit()
        logger.info(f"Updated tracker for org={tracker.org_id}, repo={tracker.repo_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating tracker: {e}")
        return False
```

## Testing Patterns

### Fixtures

Use pytest fixtures to mock the SQLAlchemy engine and connection:

```python
import pytest
from unittest.mock import MagicMock

class TestGHEOrganizationDAO:
    """Test suite for GHEOrganizationDAO class."""

    @pytest.fixture
    def mock_engine(self) -> MagicMock:
        """Mock SQLAlchemy engine with connection context manager."""
        engine = MagicMock()
        conn = MagicMock()
        engine.connect.return_value.__enter__.return_value = conn
        engine.connect.return_value.__exit__.return_value = None
        return engine

    @pytest.fixture
    def dao(self, mock_engine: MagicMock) -> GHEOrganizationDAO:
        """Create DAO instance with mock engine."""
        return GHEOrganizationDAO(mock_engine)

    def test_constructor(self, mock_engine: MagicMock) -> None:
        """Test DAO constructor stores engine."""
        dao = GHEOrganizationDAO(mock_engine)
        assert dao._engine is mock_engine
```

### Test Coverage Requirements

**For simple DAOs (15+ tests):**
- Constructor test
- Find: found, not found, database error
- Insert: success, database error
- Update: success, database error
- Delete: success, database error
- Upsert: insert path, update path, update-from-deleted path, find error, insert error, update error

**For DAOs with batch operations (25+ tests):**
- All simple DAO tests
- Batch upsert: empty list, single chunk, multiple chunks, chunk processing error

**For tracker DAOs (10+ tests):**
- Get cutoff: found, not found, database error
- Upsert: insert path, update path, error handling

### Example Test

```python
def test_find_success(
    self, dao: GHEOrganizationDAO, mock_engine: MagicMock
) -> None:
    """Test find returns org when found."""
    conn = mock_engine.connect.return_value.__enter__.return_value
    mock_row = MagicMock()
    mock_row._mapping = {
        "id": 1,
        "org": "airbnb",
        "org_id": 42,
        "deleted_at": None,
    }
    result = MagicMock()
    result.fetchone.return_value = mock_row
    conn.execute.return_value = result

    org = dao.find_ghe_org(42)

    assert org is not None
    assert org.id == 1
    assert org.org == "airbnb"
    assert org.org_id == 42
    assert org.deleted_at is None
```

## Integration Example

### Service Initialization

```python
from common.utils.db_utils import create_long_lived_engine
from common.daos import GHEOrganizationDAO, GHEPRTrackerDAO
from common.models.mysql_config import MySQLConfig
from common.models.ghe_organization import GHEOrganization

# Service startup
mysql_config = MySQLConfig.from_env()
engine = create_long_lived_engine(mysql_config)  # Auto-refreshes IAM tokens

# Create DAOs
org_dao = GHEOrganizationDAO(engine)
tracker_dao = GHEPRTrackerDAO(engine)
```

### Graceful Error Handling

```python
# Single record operation
org = GHEOrganization(org="airbnb", org_id=123456)
org_id = org_dao.insert_or_update_ghe_org(org)

if org_id is not None:
    logger.info(f"Organization ready with id={org_id}")
else:
    logger.warning("Failed to save organization, continuing without persistence")
    # Continue execution - system doesn't halt

# Batch operation
prs = fetch_pull_requests_from_api()
affected = pr_dao.upsert_batch(prs)

if affected is not None:
    logger.info(f"Successfully upserted {affected} PRs")
else:
    logger.error("Batch upsert failed, some data may be missing")

# Tracker operation
cutoff = tracker_dao.get_tracker_cutoff(org_id, repo_id)
if cutoff is not None:
    logger.info(f"Resuming from cutoff: {cutoff}")
    # Fetch only PRs after cutoff
else:
    logger.info("No tracker found, performing full historical crawl")
    # Fetch all historical PRs
```

## Checklist for New DAOs

### SQLModel Definition (in `common/models/`)

- [ ] SQLModel class with `table=True` in `common/models/{resource}.py`
- [ ] `__tablename__` set to match migration file
- [ ] `__table_args__ = {"extend_existing": True}` included
- [ ] All fields use `sa_column=Column(...)` for SQLAlchemy types
- [ ] Types match migration: `BigInteger`, `String(n)`, `DateTime`, `Integer`
- [ ] Primary key: `id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))`
- [ ] Field descriptions added for documentation
- [ ] Timestamp fields have `@field_validator` for UTC parsing if needed
- [ ] Model exported from `common/models/__init__.py`

### Basic DAO (in `common/daos/`)

- [ ] DAO accesses table via `Model.__table__  # type: ignore[attr-defined]`
- [ ] All methods have type hints (`Model | None`, `int | None`, `bool`)
- [ ] All methods have docstrings with Args/Returns sections
- [ ] Error handling: catch `Exception`, log with context, return `None`/`False`
- [ ] Find returns `None` for not-found (not an error)
- [ ] Tests cover success and error cases for each method
- [ ] mypy passes with no errors
- [ ] ruff check passes with no warnings
- [ ] DAO imported in `common/daos/__init__.py`
- [ ] DAO added to `__all__` list in alphabetical order

### Batch Operations (if bulk data source)

Batch upsert is **inherited** from `BaseUpsertDAO` — you do not implement it.

- [ ] DAO subclasses `BaseUpsertDAO` and passes its `DataSourceSpec` via
      `super().__init__(get_source("<src>"), engine, metrics)`
- [ ] A `DataSourceSpec` is registered for the source (`conflict_keys` match the
      table's unique constraint; immutable columns listed in `exclude_columns`)
- [ ] Optional source-friendly alias (e.g. `upsert_<src>_batch`) delegating to
      `self.upsert_batch(...)`
- [ ] The spec's `dao_factory` returns the DAO
- [ ] Tests: batch upsert behavior is covered by the base + spec tests; the DAO's
      own tests cover its finders

### Tracker DAO (if incremental crawling)

- [ ] Tracker SQLModel in `common/models/{resource}_tracker.py`
- [ ] Separate tracker DAO file: `common/daos/{resource}_tracker_dao.py`
- [ ] DAO accesses table via `TrackerModel.__table__  # type: ignore[attr-defined]`
- [ ] `get_tracker_cutoff()` method (returns `datetime | None`)
- [ ] `upsert_tracker()` method (returns `bool`)
- [ ] `get_all_trackers()` method for monitoring (returns `list[Model] | None`)
- [ ] Private `_insert_tracker()` and `_update_tracker()` helpers
- [ ] Separate test file: `common/daos/{resource}_tracker_dao_test.py`
- [ ] Tests for get (found/not found), upsert (insert/update paths)
- [ ] Both model and DAO exported from their respective `__init__.py` files

## Reference Implementation

**Shared base + spec (start here):**
- Base upsert DAO: `matik/common/daos/base_dao.py` (`BaseUpsertDAO`)
- Spec contract: `matik/common/datasources/registry.py` (`DataSourceSpec`,
  `conflict_keys` / `update_columns` / `llm_columns`)
- Concrete spec: `matik/common/datasources/incidentio.py`

**Concrete DAOs (subclass `BaseUpsertDAO`; finders + a batch alias):**
- Incident.io: `matik/common/daos/incidentio_incident_dao.py` (with
  `upsert_incidents_batch` delegating to `upsert_batch`) + `..._test.py`
- GHE PR: `matik/common/daos/ghe_pr_dao.py` (with `upsert_ghe_prs_batch`) + `..._test.py`
- JIRA: `matik/common/daos/jira_issues_dao.py` + `..._test.py`

**Tracker DAOs (incremental crawling; read/written via the API):**
- `matik/common/daos/incidentio_tracker_dao.py`, `matik/common/daos/ghe_pr_tracker_dao.py`,
  `matik/common/daos/jira_batch_tracker_dao.py`

**Database Utilities:**
- Connection management: `matik/common/utils/db_utils.py`
- Timestamp utilities: `matik/common/utils/datetime_utils.py`
- See [Database Documentation](database.md) for connection patterns, and
  [Onboarding a Data Source](onboarding-a-data-source.md) for the end-to-end wiring
