# SQLModel Design Guide

## Overview

This guide documents the SQLModel patterns used in the Matik platform. SQLModel combines Pydantic validation with SQLAlchemy table definitions, providing type-safe database models with runtime validation.

### What is SQLModel?

SQLModel is a library built on top of **Pydantic v2** and **SQLAlchemy 2.x** that allows you to define models once that work as:
- **Pydantic models** for validation and serialization
- **SQLAlchemy table definitions** for database operations
- **Type hints** for IDE autocomplete and type checking

###Why SQLModel?

We use SQLModel instead of pure Pydantic or SQLAlchemy because it:
- **Reduces boilerplate**: Define once, use everywhere
- **Type safety**: Python type hints validated at runtime
- **Data validation**: Pydantic validators ensure data quality
- **Database control**: SQLAlchemy Core for explicit SQL operations
- **Developer experience**: Better IDE support, fewer errors

### Technology Stack

- **Pydantic v2**: Data validation and settings management
- **SQLAlchemy 2.x**: Database operations via Core (not ORM)
- **MySQL**: Primary database engine
- **Location**: `matik/common/models/`

---

## Model Types

Matik uses three types of SQLModel models:

### 1. Database Entity Models (`table=True`)

Models that map directly to database tables:
- Used for persistent data storage
- Have `table=True` parameter
- Define explicit SQLAlchemy column types
- Support relationships, indexes, constraints

**Examples**: `GHEOrganization`, `IncidentIOIncident`, `JiraIssueRecord`

### 2. Config Models (no `table`)

Models for configuration and settings:
- No `table=True` parameter
- Pure Pydantic validation
- Loaded from YAML or environment variables
- Can have custom methods

**Examples**: `MySQLConfig`, `ApiConfig`, `MatikConfig`

### 3. API/Transfer Models (no `table`)

Models for external API responses and DTOs:
- No `table=True` parameter
- May have circular references
- Used for validation and transformation
- Not stored directly in database

**Examples**: `Issue`, `IssueFields`, `IssueLink` (from `jira_issue.py`)

---

## Database Entity Models Pattern

### Required Attributes

Every database entity model must have:

```python
from sqlmodel import SQLModel, Field
from sqlalchemy import BigInteger, Column, String

class MyModel(SQLModel, table=True):
    """Model description."""

    __tablename__ = "my_table"
    __table_args__ = {"extend_existing": True}  # Prevents test errors

    # Fields...
```

- **`table=True`**: Marks this as a database table
- **`__tablename__`**: Explicit table name (required)
- **`__table_args__`**: Dict with `extend_existing=True` to prevent SQLAlchemy metadata conflicts in tests

### Primary Key Pattern

Auto-increment integer primary key:

```python
id: int | None = Field(
    default=None,
    sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    description="Auto-increment database ID",
)
```

**Key points:**
- Type: `int | None` (None before insertion, int after)
- Default: `None` (database assigns value)
- Column type: `BigInteger` (BIGINT in MySQL)
- Always use `BigInteger` for IDs

### Field Definition Pattern

Use `Field(sa_column=Column(...))` for explicit SQL types:

```python
# Required string field
name: str = Field(
    sa_column=Column(String(255), nullable=False),
    description="Entity name",
)

# Optional string field
email: str | None = Field(
    default=None,
    sa_column=Column(String(255), nullable=True),
    description="Optional email address",
)

# Required field with unique constraint
org: str = Field(
    sa_column=Column(String(255), nullable=False, unique=True),
    description="Organization name (unique)",
)
```

**Pattern:**
- Required: `type = Field(sa_column=Column(..., nullable=False))`
- Optional: `type | None = Field(default=None, sa_column=Column(..., nullable=True))`
- Unique: Add `unique=True` to Column

---

## Field Types and SQL Mappings

### Integers

```python
from sqlalchemy import BigInteger, Integer

# Primary keys and foreign keys
id: int | None = Field(
    default=None,
    sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
)

# Regular integers
count: int = Field(
    sa_column=Column(Integer, nullable=False),
)
```

**Use `BigInteger` for**: IDs, foreign keys, large counters

### Strings

```python
from sqlalchemy import String, Text

# Short strings (typical lengths: 50, 100, 255, 500)
name: str = Field(
    sa_column=Column(String(255), nullable=False),
)

# LLM-generated summaries
summary: str | None = Field(
    default=None,
    sa_column=Column(String(3072), nullable=True),
)

# Long text content
description: str | None = Field(
    default=None,
    sa_column=Column(Text, nullable=True),
)
```

**Common lengths:**
- 50-100: Codes, enums, short IDs
- 255: Names, titles, references
- 500: Longer titles, short descriptions
- 3072: LLM-generated summaries
- `Text`: Unbounded long content

### Timestamps

```python
from datetime import datetime
from sqlalchemy import TIMESTAMP, DateTime
from pydantic import field_validator
from common.utils.datetime_utils import parse_timestamp_to_utc

# Required timestamp
created_at: datetime = Field(
    sa_column=Column(DateTime, nullable=False),
    description="Creation timestamp (UTC)",
)

# Optional timestamp
deleted_at: datetime | None = Field(
    default=None,
    sa_column=Column(TIMESTAMP, nullable=True),
    description="Soft delete timestamp",
)

# Timestamp validator (place after all timestamp fields)
@field_validator("created_at", "deleted_at", mode="before")
@classmethod
def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
    """Parse and normalize timestamps to naive UTC."""
    return parse_timestamp_to_utc(v)
```

**Key points:**
- Use `TIMESTAMP` or `DateTime` (both work)
- Store as **naive UTC** (no timezone info)
- Always use `parse_timestamp_to_utc()` validator
- Validator mode: `"before"` to preprocess values

### Audit Columns (DB-managed `row_created_at` / `row_updated_at`)

Every record table carries two audit columns whose values are set **by MySQL, not
by application code**, via SQL `server_default`s:

```python
from sqlalchemy import Column, DateTime, text

row_created_at: datetime | None = Field(
    default=None,
    sa_column=Column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    ),
    description="Row insert time (DB-managed)",
)
row_updated_at: datetime | None = Field(
    default=None,
    sa_column=Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    ),
    description="Row last-update time (DB-managed)",
)
```

**Key points:**
- These are **never written by the app.** Both the DAO and the `DataSourceSpec` treat
  `id`, `row_created_at`, and `row_updated_at` as DB-managed and exclude them from
  every insert/upsert (`_DB_MANAGED_COLUMNS` in `common/daos/base_dao.py` and
  `common/datasources/registry.py`). Declaring them in the model is only so the
  schema/migration includes them and reads can hydrate them.
- Include them in the table's Alembic migration with the same `server_default` DDL.
- Include the audit columns in the model's `parse_timestamp` validator field list
  (they hydrate as naive UTC on reads, like any other timestamp).

### JSON Fields (Lists and Dicts)

```python
from sqlalchemy.dialects.mysql import JSON

# List of strings
tags: list[str] | None = Field(
    default=None,
    sa_column=Column(JSON, nullable=True),
    description="Tag list",
)

# Dict (key-value pairs)
labels: dict[str, str] | None = Field(
    default=None,
    sa_column=Column(JSON, nullable=True),
    description="Label key-value pairs",
)

# Nested model stored as JSON
relations: list[GreenroomEntityRelation] | None = Field(
    default=None,
    sa_column=Column(JSON, nullable=True),
    description="Entity relationships",
)
```

**Important:**
- Use `JSON` from `sqlalchemy.dialects.mysql`
- Always mark as optional (`| None`, `nullable=True`)
- Can store lists, dicts, or nested models
- **Store native Python objects — never `json.dumps` before assigning.** The `JSON`
  column type serializes exactly once on write. Pre-encoding double-encodes the
  value (it lands as the string `'["a","b"]'` instead of the array `["a","b"]`).
  The DAO passes list columns straight through (`_derived_row` in
  `common/daos/base_dao.py`); do the same in any hand-written write path.

### Booleans

```python
from sqlalchemy import Boolean

is_active: bool | None = Field(
    default=None,
    sa_column=Column(Boolean, nullable=True),
    description="Active status flag",
)
```

### Floats

```python
from sqlalchemy import Float

score: float | None = Field(
    default=None,
    sa_column=Column(Float, nullable=True),
    description="Quality score",
)
```

### Enum-like Fields (Use Plain Strings)

```python
# ✓ CORRECT - Use plain str
status: str = Field(
    sa_column=Column(String(50), nullable=False),
    description="Status: open, investigating, closed",
)

# ✗ AVOID - Don't use Literal types
# status: Literal["open", "closed"] = Field(...)
```

**Rationale:** External APIs may add new enum values. `Literal` types would break validation.

---

## Complete Database Entity Example

```python
"""GitHub Enterprise organization data models using SQLModel."""

from datetime import datetime

from pydantic import field_validator
from sqlalchemy import TIMESTAMP, BigInteger, Column, String
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


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

    # Required fields
    org: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True),
        description="Organization login name",
    )

    org_id: int = Field(
        sa_column=Column(BigInteger, nullable=False, unique=True),
        description="GitHub organization ID",
    )

    # Soft delete timestamp
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(TIMESTAMP, nullable=True),
        description="Soft delete timestamp",
    )

    @field_validator("deleted_at", mode="before")
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        """Parse and normalize timestamps to naive UTC."""
        return parse_timestamp_to_utc(v)
```

---

## Config Models Pattern

Config models don't map to database tables - they're for configuration and settings.

### Basic Config Model

```python
"""API service configuration."""

from sqlmodel import Field, SQLModel


class ApiConfig(SQLModel):
    """API service configuration.

    Note: No table=True, this is a configuration model only.
    """

    catalog_endpoint: str = Field(
        default="http://localhost:8081",
        description="Catalog service endpoint URL",
    )
```

### Config with Custom Methods

```python
"""Database configuration model."""

from typing import Any
from sqlmodel import Field, SQLModel


class MySQLConfig(SQLModel):
    """Database configuration.

    Note: No table=True, this is a configuration model only.
    """

    engine: str = Field(..., description="Database engine type")
    endpoint: str = Field(..., description="Database endpoint")
    port: int = Field(default=3306, description="Database port")

    # Custom method
    def to_sqlalchemy_pool_kwargs(self) -> dict[str, Any]:
        """Convert pool settings to SQLAlchemy kwargs."""
        kwargs: dict[str, Any] = {}
        # Implementation...
        return kwargs
```

### Nested Config Model

```python
"""Main configuration container."""

from sqlmodel import Field, SQLModel
from common.models.mysql_config import MySQLConfig
from common.models.api_config import ApiConfig


class MatikConfig(SQLModel):
    """Main configuration class.

    Note: No table=True, this is a configuration model only.
    """

    model_config = {"extra": "allow"}  # Allow extra fields from YAML

    # Nested config models
    mysql: MySQLConfig | None = Field(default=None)
    api: ApiConfig | None = Field(default=None)

    # Dict of configs
    connector: dict[str, ConnectorConfig] = Field(default_factory=dict)
```

**Key features:**
- `model_config = {"extra": "allow"}`: Accept additional YAML fields
- `default_factory`: For mutable defaults (dict, list)
- Nested models: Compose from other SQLModel classes

---

## API/Transfer Models with Circular References

For models with circular references (e.g., JIRA issues linking to other issues):

```python
"""JIRA issue data models using SQLModel.

Note: No table=True, these are API/transfer models only.
"""

from __future__ import annotations  # Enable forward references

from datetime import datetime
from pydantic import field_validator
from sqlmodel import Field, SQLModel

from common.utils.datetime_utils import parse_timestamp_to_utc


class IssueLink(SQLModel):
    """Represents a link between issues."""

    id: str | None = Field(default=None)
    type: IssueLinkType = Field(...)

    # Forward reference to Issue (defined below)
    outward_issue: Issue | None = Field(default=None)
    inward_issue: Issue | None = Field(default=None)


class IssueFields(SQLModel):
    """Represents all fields of an issue."""

    summary: str | None = Field(default=None)
    status: Status | None = Field(default=None)

    # Circular reference - list of IssueLink (defined above)
    issuelinks: list[IssueLink] | None = Field(default=None)

    @field_validator("created", "updated", mode="before")
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        return parse_timestamp_to_utc(v)


class Issue(SQLModel):
    """Represents a JIRA issue."""

    model_config = {"from_attributes": True}  # Enable ORM mode

    id: str | None = Field(default=None)
    key: str | None = Field(default=None)
    fields: IssueFields | None = Field(default=None)


# Rebuild models to resolve forward references
IssueLink.model_rebuild()
```

**Required steps:**
1. Add `from __future__ import annotations` at top
2. Use forward references: `Issue | None`
3. Call `.model_rebuild()` at module end
4. Optional: `model_config = {"from_attributes": True}` for ORM compatibility

---

## Common Patterns

### Soft Deletes

Use a nullable `deleted_at` timestamp instead of physical deletion:

```python
deleted_at: datetime | None = Field(
    default=None,
    sa_column=Column(TIMESTAMP, nullable=True),
    description="Soft delete timestamp",
)
```

**Benefits:**
- Preserve data for audit/recovery
- Query with `WHERE deleted_at IS NULL`

### Tracker Tables (Single-Row Pattern)

For tracking incremental crawls, use a single-row table:

```python
class IncidentIOTracker(SQLModel, table=True):
    """Tracks last processed incident."""

    __tablename__ = "incidentio_tracker"
    __table_args__ = {"extend_existing": True}

    id: int = Field(
        default=1,  # Single row with id=1
        sa_column=Column(Integer, primary_key=True),
    )

    incident_id: str = Field(...)
    timestamp: datetime = Field(...)
    status: str = Field(...)
```

**Pattern:**
- Primary key defaults to `1`
- Only one row exists
- UPDATE instead of INSERT

### Array Fields (JSON Storage)

Store lists in JSON columns:

```python
from sqlalchemy.dialects.mysql import JSON

affected_services: list[str] | None = Field(
    default=None,
    sa_column=Column(JSON, nullable=True),
    description='Affected services, e.g. ["api", "web"]',
)
```

### Nested Models (JSON Storage)

Define a separate model, store as JSON:

```python
# Define nested model (no table=True)
class GreenroomEntityRelation(SQLModel):
    """Relationship between entities."""

    type: str = Field(...)
    target_ref: str = Field(...)


# Use in parent model
class GreenroomEntity(SQLModel, table=True):
    """Catalog entity."""

    __tablename__ = "greenroom_entities"

    # Store as JSON
    relations: list[GreenroomEntityRelation] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
```

### LLM-Generated Fields

For AI-generated summaries, use longer strings:

```python
root_cause_summary: str | None = Field(
    default=None,
    sa_column=Column(String(3072), nullable=True),  # 3KB
    description="LLM-generated root cause summary",
)
```

---

## Testing Patterns

### SQLModel Behavior Differences

SQLModel table models behave differently from pure Pydantic:

```python
# ✗ WRONG - Direct instantiation may skip validators
def test_validation_wrong():
    with pytest.raises(ValidationError):
        MyModel(required_field=None)  # Might not raise!

# ✓ CORRECT - Use model_validate() for validation tests
def test_validation_correct():
    with pytest.raises(ValidationError):
        MyModel.model_validate({"required_field": None})
```

### Auto-Increment PK Defaults

Primary keys default to `None` before database insertion:

```python
def test_id_default():
    org = GHEOrganization(org="airbnb", org_id=123)
    assert org.id is None  # Not 0!
```

**Exception:** Single-row trackers have `id = 1`

### Timestamp Parsing Tests

Validators only run during `.model_validate()`:

```python
def test_timestamp_parsing():
    # Use model_validate to trigger validators
    org = GHEOrganization.model_validate({
        "org": "airbnb",
        "org_id": 123,
        "deleted_at": "2024-01-09T10:30:00Z",  # ISO string
    })

    assert isinstance(org.deleted_at, datetime)
    assert org.deleted_at.tzinfo is None  # Naive UTC
```

---

## Examples

### Simple Database Entity

**`GHEOrganization`** (`common/models/ghe_organization.py`)
- Basic table with soft delete
- Auto-increment primary key
- Unique constraints
- Timestamp validator

### Complex Database Entity

**`IncidentIOIncident`** (`common/models/incidentio_incident.py`)
- Multiple timestamps (lifecycle stages)
- JSON array fields
- Boolean flags
- LLM-generated summaries (long strings)
- Field renaming (plural forms)

### Config Model

**`MySQLConfig`** (`common/models/mysql_config.py`)
- No table mapping
- Custom method (`to_sqlalchemy_pool_kwargs()`)
- IAM authentication support
- Connection pool settings

### Nested Config

**`MatikConfig`** (`common/models/matik_config.py`)
- Composition of multiple config models
- `extra="allow"` for flexible YAML
- `default_factory` for mutable defaults

### API Model with Circular References

**`jira_issue.py`** (`common/models/jira_issue.py`)
- `from __future__ import annotations`
- Forward references
- `.model_rebuild()` at module end
- Nested structures

### Nested Model

**`GreenroomEntityRelation`** (`common/models/greenroom_entity.py`)
- Used within `GreenroomEntity`
- Stored as JSON in database
- No `table=True`

---

## Best Practices

### Do's

✓ Use `BigInteger` for all IDs (PKs, FKs)

✓ Add `description=` to all fields

✓ Use `parse_timestamp_to_utc()` for timestamps

✓ Mark array/dict fields as optional (`| None`)

✓ Use `__table_args__ = {"extend_existing": True}`

✓ Use plain `str` for enum-like fields

✓ Validate with `.model_validate()` in tests

### Don'ts

✗ Don't use `Literal` types for enums (API flexibility)

✗ Don't forget timestamp validators

✗ Don't use `Integer` for IDs (use `BigInteger`)

✗ Don't skip `__tablename__` (required)

✗ Don't assume validators run on direct instantiation

---

## Related Documentation

- **DAO Patterns**: `_infra/docs/development/dao.md` - How to use SQLModel models with SQLAlchemy Core DAOs
- **Database Guide**: `_infra/docs/development/database.md` - Database connection and migration patterns
- **Naming Conventions**: `_infra/docs/development/naming-conventions.md` - Python naming standards

---

## References

- **SQLModel Docs**: https://sqlmodel.tiangolo.com/
- **Pydantic v2**: https://docs.pydantic.dev/latest/
- **SQLAlchemy 2.x**: https://docs.sqlalchemy.org/en/20/

**Location**: `matik/common/models/`
**Dependency**: `sqlmodel>=0.0.22` (includes Pydantic v2 + SQLAlchemy 2.x)
