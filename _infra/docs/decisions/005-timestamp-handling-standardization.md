# Matik - Timestamp Handling Standardization

Date: 2025-11-17

Status: `Accepted`

Collaborators: @sumit-chachadi

## Context

Matik integrates with multiple external data sources (Incident.io, PagerDuty, JIRA, GitHub, AWS) that provide timestamp data in various formats and timezones. The platform stores this temporal data in MySQL DATETIME columns.

Prior to this decision, timestamp handling was inconsistent across the codebase:
- Some services stored timestamps with timezone information, others didn't
- Different formats were used (RFC3339, ISO8601, custom formats)
- No standardization for converting external API timestamps
- Implicit timezone assumptions led to ambiguity
- MySQL DATETIME columns don't store timezone information, causing confusion

This inconsistency created several problems:
1. **Data Quality Issues**: Timestamps from different sources couldn't be reliably compared
2. **Developer Confusion**: No clear guidance on how to handle timestamps correctly
3. **Testing Challenges**: Timezone-dependent bugs were difficult to reproduce and test

### Option I - Store Native Timezones with TIMESTAMP Type

Use MySQL TIMESTAMP columns which store timezone information and convert to UTC automatically.

#### Pros
- Database handles timezone conversions automatically
- Clear timezone semantics at database level
- Less application-layer code required
- TIMESTAMP type converts to connection timezone

#### Cons
- MySQL TIMESTAMP limited to years 1970-2038 (Y2038 problem)
- Performance overhead for timezone conversions
- Requires database schema migration for all existing tables
- Different behavior across MySQL versions
- Connection timezone must be carefully managed
- Harder to audit raw data in database

### Option II - UTC Standardization with RFC3339 Format (Application Layer)

Standardize all timestamps to UTC timezone using RFC3339 format (ISO 8601 compliant) at the application layer, storing as strings in MySQL DATETIME columns.

#### Pros
- **No schema changes required**: Works with existing DATETIME columns
- **Explicit and auditable**: Raw data in database is in clear, standard format
- **Universal compatibility**: RFC3339 is widely supported across languages and systems
- **Future-proof**: No year 2038 problem
- **Consistent external API integration**: Single format for all external data sources
- **Developer clarity**: Clear rules that can be documented and enforced
- **Testability**: Easy to create test fixtures with known UTC timestamps
- **Performance**: No database-level timezone conversions required
- **Portability**: Data can be moved between databases without timezone concerns

#### Cons
- Requires discipline in application code
- Must validate and convert all incoming timestamps
- More application-layer code to maintain
- Developers must remember to use utility functions

### Option III - Store as Unix Timestamps (Integers)

Store all timestamps as Unix epoch seconds (integer type).

#### Pros
- Efficient storage and indexing
- Simple arithmetic operations
- No timezone ambiguity
- Compact representation

#### Cons
- Not human-readable in database
- Difficult to debug and audit
- Loses sub-second precision unless using milliseconds/microseconds
- Harder to write SQL queries with date arithmetic
- External APIs typically don't provide Unix timestamps
- Y2038 problem with 32-bit integers

## Decision

**Option II: UTC Standardization with RFC3339 Format at Application Layer**

All timestamps in Matik will be:
1. Converted to UTC timezone immediately upon receipt from external APIs
2. Formatted using RFC3339 format (`2024-01-15T10:30:00Z`)
3. Stored as strings in MySQL DATETIME columns
4. Handled using standardized utility functions in `common/utils/utils.go`

## Considerations

### Why UTC?
- **Single source of truth**: UTC is the universal time standard
- **No DST complications**: UTC doesn't observe daylight saving time
- **Industry standard**: Most distributed systems and APIs use UTC internally
- **LLM analysis**: Consistent timestamps improve pattern detection across time periods


### Implementation Strategy
1. **Create utility functions** in `common/utils/utils.go`:
   - `ToUTC(t time.Time) time.Time` - Convert to UTC for database writes (MySQL handles time.Time directly)
   - `ParseTimestamp(s string) (time.Time, error)` - Parse external API timestamp strings
   - `NowUTC() time.Time` - Get current time in UTC
   - `ToUTCOrNil(t *time.Time) interface{}` - Handle nullable timestamps for database writes (returns time.Time value or nil)
   - `ParseNullableTimestamp(s string) (*time.Time, error)` - Parse nullable timestamps from external APIs
   - `TimestampFormat` constant - Standard RFC3339 format for external API communication

2. **DAO Layer Pattern**:
   - Always format timestamps before database writes
   - Always convert to UTC after database reads
   - Use nullable helper functions for optional timestamps

3. **Client Layer Pattern**:
   - Convert external API timestamps to UTC immediately upon receipt
   - Validate timestamp formats from external sources
   - Log warnings for invalid timestamp formats (preserve data, don't fail)

4. **Model Design**:
   - Prefer `time.Time` and `*time.Time` over string types for timestamps
   - Only use string types when required by external APIs
   - Always validate and normalize string timestamps before storage

5. **Documentation**:
   - Document patterns in CLAUDE.md for AI assistance
   - Create decision doc in `_infra/docs/decisions/`

## Consequences

### Positive
- **Consistency**: All timestamps stored and processed in consistent format
- **Reliability**: No more timezone-related bugs in incident timeline analysis
- **Maintainability**: Clear patterns for all developers to follow
- **Testability**: Predictable timestamp handling makes tests more reliable
- **Debuggability**: Human-readable timestamps in database and logs
- **Integration**: External API timestamps normalized to common format
- **LLM Quality**: Consistent timestamps improve AI/ML pattern detection
- **Future-proof**: No Y2038 problem, works with all date ranges

### Negative
- **Developer discipline**: Requires developers to use utility functions consistently
- **String storage**: Slightly less efficient than integer timestamps (minimal impact)
- **Validation overhead**: Must validate timestamps from external sources

### Technical Debt Addressed
- Fixed inconsistent timestamp handling in DAO layer
- Standardized external API timestamp conversions
- Resolved timezone ambiguity issues
- Improved data quality for temporal analysis

## Examples

### Database Write Example
```go
// Before: Inconsistent, implicit timezone
Values(incident.CreatedAt, incident.ClosedAt)

// After: Explicit UTC conversion (MySQL driver accepts time.Time value or nil)
Values(
    utils.ToUTC(incident.CreatedAt),
    utils.ToUTCOrNil(incident.ClosedAt),
)
```

### Database Read Example
```go
// Before: Implicit timezone, potential for timezone bugs
err = db.QueryRow(query).Scan(&incident.CreatedAt)

// After: Explicit UTC conversion
err = db.QueryRow(query).Scan(&incident.CreatedAt)
incident.CreatedAt = incident.CreatedAt.UTC()
```

### External API Integration Example
```go
// Before: Unknown timezone, no validation
incident.CreatedAt = apiResponse.CreatedAt

// After: Validate, convert to UTC, standardize format
parsed, err := utils.ParseTimestamp(apiResponse.CreatedAt)
if err != nil {
    airlog.Warn().Msgf("Invalid timestamp: %v", err)
    // Handle error appropriately
}
incident.CreatedAt = parsed.UTC()
```

### Current Time Example
```go
// Before: Local timezone, implicit
tracker.TimeStamp = time.Now()

// After: Explicit UTC
tracker.TimeStamp = time.Now().UTC()
```

## Review and Maintenance
Last reviewed: 2025-11-17
Next review: 2026-02-17 (3 months)
