# SQLModel Adoption for Matik Data Layer

Date: 2026-01-14

Status: `accepted`

Collaborators: @sumit_chachadi

## Context

Matik's Python data layer currently maintains duplicate definitions:
- **Pydantic models** (`matik/common/models/`) for validation and serialization (26 models)
- **SQLAlchemy Core tables** (`matik/common/daos/`) for database operations

This duplication creates maintenance overhead:
- Schema changes require updating both model and table definitions
- Field mappings between models and tables must be manually synchronized
- ~50% redundant code across the data layer

**SQLModel** is a library that unifies Pydantic and SQLAlchemy, allowing a single class to serve as both a validation model and database table definition. This research evaluates the feasibility of adopting SQLModel to consolidate our data layer.

## Summary of Findings

### Comprehensive Codebase Analysis

Analyzed 26 Pydantic models, SQLAlchemy Core DAOs, and 16 database migrations:

| Model Type | Count | Examples |
|------------|-------|----------|
| Database entities | 12 | `IncidentIOIncident`, `GHEOrganization`, `GHEPullRequest` |
| Configuration models | 9 | `MatikConfig`, `DBConfig`, `CommonConfig` |
| API/Transfer models | 5 | `Issue`, `IssueFields`, `IssueLink` (JIRA) |

### Compatibility Assessment: ✅ HIGHLY FEASIBLE

All current patterns are compatible with SQLModel:

| Pattern | Current Implementation | SQLModel Support |
|---------|----------------------|------------------|
| **Field validators** | `@field_validator` with `parse_timestamp_to_utc()` | ✅ Identical (inherits Pydantic v2) |
| **ORM mode** | `model_config = {"from_attributes": True}` | ✅ Built-in (table models auto-enable) |
| **Type hints** | Python 3.11+ syntax (`str \| None`) | ✅ Full support |
| **JSON columns** | `list[str]`, `dict[str, Any]` | ✅ Transparent serialization |
| **Nested models** | `IssueFields`, `GreenroomEntityRelation` | ✅ Supported |
| **Circular refs** | `Issue` ↔ `IssueLink` with `.model_rebuild()` | ✅ Same pattern |
| **Soft deletes** | `deleted_at: datetime \| None` | ✅ Standard field |

### Schema Mismatches (Model ≠ Database)

Five schema discrepancies identified:

| Model Field | DB Column | Issue | SQLModel Solution |
|-------------|-----------|-------|-------------------|
| `IncidentIOIncident.root_cause_change_type` | `root_cause_change_types` | Singular vs plural | `sa_column=Column("root_cause_change_types")` |
| `IncidentIOIncident.detection_method` | `detection_methods` | Singular vs plural | `sa_column=Column("detection_methods")` |
| `GHEPullRequest.jira_tcmr_id: int` | `jira_tcmr_link: TEXT` | Type mismatch | Change model to `jira_tcmr_link: str \| None` |
| `ConnectorType.enabled` | (missing) | Model-only field | `Field(..., exclude=True)` |
| `ConnectorProvider` (missing enabled) | `enabled BOOLEAN` | DB-only field | Add to model with default |

All mismatches have clean resolutions using SQLModel's field customization.

### Code Reduction Analysis

Current approach (duplicated):
```python
# models/ghe_organization.py
class GHEOrganization(BaseModel):
    model_config = {"from_attributes": True}
    id: int = Field(default=0)
    org: str = Field(...)
    org_id: int = Field(...)
    deleted_at: datetime | None = Field(default=None)

# daos/ghe_organization_dao.py
ghe_organizations_table = Table(
    "ghe_organizations",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("org", String(255), nullable=False, unique=True),
    Column("org_id", BigInteger, nullable=False, unique=True),
    Column("deleted_at", TIMESTAMP, nullable=True),
)
```

SQLModel approach (unified):
```python
# models/ghe_organization.py
class GHEOrganization(SQLModel, table=True):
    __tablename__ = "ghe_organizations"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    org: str = Field(sa_column=Column(String(255), nullable=False, unique=True))
    org_id: int = Field(sa_column=Column(BigInteger, nullable=False, unique=True))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(TIMESTAMP))

    @field_validator("deleted_at", mode="before")
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        return parse_timestamp_to_utc(v)
```

**Result**: ~50% code reduction, single source of truth.

## Key SQLModel Patterns for Matik

### Pattern 1: Simple Database Entity with Validators

```python
from sqlalchemy import BigInteger, Column, String, TIMESTAMP
from sqlmodel import Field, SQLModel
from pydantic import field_validator
from common.utils.datetime_utils import parse_timestamp_to_utc

class GHEOrganization(SQLModel, table=True):
    """GitHub Enterprise organization."""
    __tablename__ = "ghe_organizations"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    org: str = Field(sa_column=Column(String(255), nullable=False, unique=True))
    org_id: int = Field(sa_column=Column(BigInteger, nullable=False, unique=True))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(TIMESTAMP))

    @field_validator("deleted_at", mode="before")
    @classmethod
    def parse_timestamp(cls, v: str | datetime | None) -> datetime | None:
        return parse_timestamp_to_utc(v)
```

### Pattern 2: Column Name Mapping (Field ≠ Column)

```python
from sqlalchemy.dialects.mysql import JSON

class IncidentIOIncident(SQLModel, table=True):
    # Model uses singular, database uses plural
    root_cause_change_type: list[str] | None = Field(
        default=None,
        sa_column=Column("root_cause_change_types", JSON, nullable=True),
    )

    detection_method: list[str] | None = Field(
        default=None,
        sa_column=Column("detection_methods", JSON, nullable=True),
    )
```

### Pattern 3: Model-Only Fields (Not Persisted)

```python
class ConnectorType(SQLModel, table=True):
    # Persisted fields
    name: str = Field(...)
    display_name: str = Field(...)

    # Runtime-only fields
    enabled: bool = Field(default=True, exclude=True)
    cutoff_date: datetime | None = Field(default=None, exclude=True)
```

### Pattern 4: Non-Table Models (Config)

```python
class DBConfig(SQLModel):
    """No table=True, behaves exactly like Pydantic BaseModel."""
    engine: str = Field(...)
    endpoint: str = Field(...)
    port: int = Field(default=3306)

    def to_sqlalchemy_pool_kwargs(self) -> dict[str, Any]:
        """Custom methods work normally."""
        ...
```

### Pattern 5: Circular References (JIRA)

```python
from __future__ import annotations

class IssueLink(SQLModel):
    outward_issue: "Issue | None" = Field(default=None)
    inward_issue: "Issue | None" = Field(default=None)

class Issue(SQLModel):
    fields: IssueFields | None = Field(default=None)

# Resolve forward references
IssueLink.model_rebuild()
Issue.model_rebuild()
```

## DAO Improvements with SQLModel

### Current (SQLAlchemy Core)

```python
def find_by_org_id(self, org_id: int) -> GHEOrganization | None:
    stmt = select(ghe_organizations_table).where(
        ghe_organizations_table.c.org_id == org_id
    )
    with self._engine.connect() as conn:
        result = conn.execute(stmt).fetchone()
        if result:
            return GHEOrganization.model_validate(result._mapping)
        return None
```

### With SQLModel

```python
def find_by_org_id(self, org_id: int) -> GHEOrganization | None:
    with Session(self.engine) as session:
        stmt = select(GHEOrganization).where(GHEOrganization.org_id == org_id)
        return session.exec(stmt).first()
```

**Improvements**:
- Direct model reference (no separate table object)
- Automatic ORM mapping (no `.model_validate()`)
- Cleaner session API
- Type-safe query building

## Decision

**Adopt SQLModel for Matik's data layer.**

### Rationale

1. **Zero Compatibility Blockers**: All 26 models migrate cleanly (validators, type hints, nested models, circular refs)
2. **50% Code Reduction**: Single definition replaces duplicated Pydantic + SQLAlchemy table code
3. **Improved Maintainability**: Schema changes update in one place
4. **Better DX**: Session-based DAOs are cleaner than explicit Table definitions
5. **Future-Proof**: SQLModel actively maintained, official Pydantic v2 + SQLAlchemy 2.x support

### User Requirements (Confirmed)

- ✅ Convert all models (including config models without tables)
- ✅ Document table-only/model-only fields using `exclude=True`
- ✅ Full DAO rewrite with session-based operations
- ✅ Stay synchronous (no async migration)

### Dependencies

```toml
dependencies = [
    "sqlmodel>=0.0.22",      # New - combines Pydantic + SQLAlchemy
    "pydantic>=2.10.4",      # Already present
    "sqlalchemy>=2.0.0",     # Already present
]
```

## Consequences

### Positive

1. **50% less data layer code**: Single class definition instead of duplicated Model + Table
2. **Reduced sync errors**: Schema changes update in one place
3. **Cleaner DAOs**: Session-based operations vs explicit Table objects
4. **Type safety**: Full type hints for database operations
5. **Developer velocity**: Faster feature development with less boilerplate

### Negative

1. **Learning curve**: Team needs to learn SQLModel patterns (minimal - mostly Pydantic + SQLAlchemy)

### Neutral

1. **No performance change**: SQLModel is thin wrapper over Pydantic + SQLAlchemy
2. **No async**: Staying synchronous matches current architecture
3. **Testing effort**: Must rewrite tests for new DAOs (opportunity to improve coverage)

### Mitigation

- **Parallel modules**: Keep old code working during migration
- **Incremental rollout**: Migrate simple models first, build confidence
- **Comprehensive testing**: Unit tests (SQLite) + integration tests (MySQL)
- **Documentation**: Update CLAUDE.md with SQLModel patterns

## References

- **SQLModel**: https://sqlmodel.tiangolo.com/
- **Current models**: `matik/common/models/`
- **Current DAOs**: `matik/common/daos/`
- **Timestamp utils**: `matik/common/utils/datetime_utils.py`
- **Related decision**: [009-language-decision-python.md](009-language-decision-python.md)
