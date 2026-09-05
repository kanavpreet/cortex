# Historian Services Design Guide

## Overview

Historian services are batch jobs that crawl external data sources and persist data to the Matik platform. Each historian is responsible for fetching historical data from a specific source (e.g., GitHub Enterprise, JIRA, Incident.io) and storing it via the Matik API.

### Technology Stack

- **Python 3.13+** for all historian implementations
- **dataclasses** for crawler class structure
- **asyncio** for high-volume crawlers (optional based on use case)
- **Pydantic** for data validation via SQLModel
- **MatikApiClient** for internal API calls to persist data
- **Location:** `matik/historian/<source_name>/`

### Design Principles

1. **Configuration-driven**: All parameters come from YAML config and environment variables
2. **Records via Scribe, trackers via the API**: base records are published to the Scribe SQS queue (Scribe is the sole DB writer); the crawler's sync tracker is read/written over the Matik API. Historians never write record tables directly.
3. **Incremental crawling**: Use trackers to avoid re-processing unchanged data
4. **Enrichment is the Enricher's job**: historians forward an enrichment request for every record to the Enricher SQS queue and never call the LLM/Facade themselves. Hash-based change detection (skip re-summarizing unchanged content) lives in the Enricher, and the enricher config is required (the historian hard-fails without it).
5. **Graceful error handling**: Log errors, continue processing other records

---

## Async vs Sync Decision Guide

Not all historians need to be async. Choose based on your data source characteristics:

### When to Use Async

Use async when your historian needs to:
- Make **high volume** of API requests (hundreds or thousands per run)
- Process multiple independent entities in parallel
- Handle rate limits by processing other items while waiting
- Benefit from concurrent I/O operations

**Example:** GHE historian crawls multiple organizations with many repositories and PRs. Async allows fetching from multiple repos concurrently.

### When to Use Sync

Use sync when your historian:
- Makes **low volume** of requests (tens per run)
- Processes data sequentially with no parallelization benefit
- Has simple, linear data flow
- Uses external libraries that are synchronous

**Example:** JIRA historian fetches limited tickets with pagination. Sequential processing is sufficient and simpler to implement.

### Code Pattern Comparison

**Async Pattern (GHE Historian):**
```python
import asyncio
from dataclasses import dataclass

@dataclass
class MyAsyncCrawler:
    external_client: MyExternalClient  # async client
    matik_client: MatikApiClient       # sync client (wrapped with to_thread)

    async def run(self) -> int:
        total = 0
        for org in self.config.organizations:
            count = await self.process_organization(org)
            total += count
        return total

    async def _post_data(self, data: MyModel) -> MyModel:
        # Wrap sync MatikApiClient call with asyncio.to_thread
        response_bytes = await asyncio.to_thread(
            self.matik_client.post_json_request,
            "/v1/my/endpoint/",
            data.model_dump(mode="json"),
        )
        return MyModel.model_validate(json.loads(response_bytes))

def main() -> int:
    return asyncio.run(run())
```

**Sync Pattern (JIRA Historian):**
```python
from dataclasses import dataclass

@dataclass
class MySyncCrawler:
    external_client: MyExternalClient  # sync client
    matik_client: MatikApiClient       # sync client

    def run(self) -> int:
        total = 0
        for project in self.config.projects:
            count = self.process_project(project)
            total += count
        return total

    def _post_data(self, data: MyModel) -> MyModel:
        # Direct sync call
        response_bytes = self.matik_client.post_json_request(
            "/v1/my/endpoint/",
            data.model_dump(mode="json"),
        )
        return MyModel.model_validate(json.loads(response_bytes))

def main() -> int:
    crawler = MySyncCrawler(...)
    return 0 if crawler.run() > 0 else 1
```

---

## Tracker Pattern: Lookback vs No Lookback

Trackers enable incremental crawling by storing the last successful crawl position. The lookback strategy depends on whether your data is mutable or immutable.

### With Lookback (Mutable Data)

Use lookback when data can be **updated after creation**:
- Pull requests (comments added, descriptions edited)
- JIRA tickets (status changes, comments)
- Any record with fields that change over time

**Pattern:**
```python
# Get tracker cutoff (with lookback applied)
tracker = await self._get_tracker(org_id, repo_id)
if tracker:
    # Lookback: re-crawl recent data that may have been updated
    cutoff = tracker.cutoff_date
else:
    cutoff = self._get_configured_cutoff()

# After successful crawl, update tracker with NEW cutoff
lookback_days = self._get_tracker_lookback_days()  # e.g., 30 days
new_cutoff = utc_now_naive() - relativedelta(days=lookback_days)
await self._update_tracker(org_id, repo_id, new_cutoff, prs_crawled)
```

**Configuration:**
```yaml
connector_conf:
  biztech_github:
    tracker_lookback_days: 30  # Re-crawl last 30 days each run
```

**Why:** A PR created 3 months ago may have description edits and change in status. The lookback ensures we catch these updates.

### Without Lookback (Immutable Data)

Use no lookback when data is **immutable after creation**:

**Pattern:**
```python
# Get tracker cutoff (no lookback - just use last crawl time)
tracker = await self._get_tracker(source_id)
if tracker:
    cutoff = tracker.last_crawled_at  # Start from where we left off
else:
    cutoff = self._get_configured_cutoff()

# After successful crawl, update tracker to current time
await self._update_tracker(source_id, utc_now_naive(), records_crawled)
```

**Configuration:**
```yaml
connector_conf:
  datasource_x:
    # No tracker_lookback_days - only crawl new records
    cutoff_date: "2023-06-01T00:00:00Z"
```

**Why:** Once an entity is created, its core data doesn't change. No need to re-fetch old records.

---

## File Structure

Each historian lives in its own package under `matik/historian/`:

```
matik/historian/<source_name>/
├── __init__.py              # Package marker (can be empty)
├── __main__.py              # Entry point for `python -m historian.<source_name>`
├── main.py                  # Main logic: config loading, client creation, run
├── <source>_crawler.py      # Crawler class implementation
└── <source>_crawler_test.py # Unit tests for crawler
```

### Example: GHE Historian

```
matik/historian/biztech_github/
├── __init__.py
├── __main__.py
├── main.py
├── ghe_pr_crawler.py
└── ghe_pr_crawler_test.py
```

---

## Implementation Patterns

Since the "paved path" refactor, every historian shares one entry-point shell,
`run_historian` (`historian/base/runner.py`), and the common producer/consumer
crawl skeleton lives in `BaseCrawler` (`historian/base/crawler.py`). A source's
`main.py` is a thin wiring block, not a ~230-line copy-paste. There is **no inline
LLM/Facade call** in any historian — the Enricher owns enrichment; historians
forward enrichment requests to the Enricher SQS queue and **hard-fail if the
enricher config is missing**.

### Entry Point (`__main__.py`)

Two lines, for `python -m historian.<source>`:

```python
import sys
from .main import main

sys.exit(main())
```

### `main.py` — a `build_and_run` callback + one `run_historian` call

`run_historian` owns the shared shell: base-config load, the ordered config-file
merges, logging, the source/API/**enricher** validation (hard-fail), Telescope +
metrics setup, and shutdown. It hands the source a `HistorianContext` (`config`,
`source_config`, `meter`, `service_name`) and calls the source's
`build_and_run(ctx) -> int`, which builds the source's clients/publishers/metrics
and runs its crawler.

```python
"""<Source> Historian entry point."""
import sys

from common.datasources.registry import get_source
from historian.base.runner import HistorianContext, run_historian
from .source_crawler import SourceCrawler


def _build_and_run(ctx: HistorianContext) -> int:
    config = ctx.config
    # build metric objects from ctx.meter / ctx.service_name (when Telescope is on),
    # the source API client, the matik-api client, a base-record SQSPublisher, and an
    # EnrichmentPublisher (to config.enricher.enricher_queue_url) ...
    crawler = SourceCrawler(config=config.source, matik_client=..., source_client=...,
                            sqs_publisher=..., enrichment_publisher=..., job_metrics=...)
    result = crawler.dispatch()            # -> CrawlerResult
    return 0 if result.success else 1


def main() -> int:
    return run_historian(
        spec=get_source("<source>"),
        caller_name=__name__,
        source_config_files=[
            ("matik-historian-<source>-config.yml", "Failed to load config"),
            ("metrics.yml", "Failed to load metrics config"),
            ("matik-enricher-config.yml", "Failed to load enricher config"),
        ],
        build_and_run=_build_and_run,
        source_config_missing_msg="<source> configuration not found",
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

Reference: `historian/incidentio/main.py`. Note `.yml` (not `.yaml`). If the
source's config-section name differs from its `source_type`, pass
`source_config_attr=` (GHE: `source_type="ghe_pr"` but `config.biztech_github`).

### Crawler: subclass `BaseCrawler`, or expose your own `dispatch()`

Every crawler exposes `dispatch() -> CrawlerResult` (`historian/base/crawler.py`).
There are two ways to satisfy that contract:

**1. Subclass `BaseCrawler` (recommended for streaming sources — copy
`historian/incidentio/incidentio_incident_crawler.py`).** You inherit the
producer→bounded-queue→consumer→batch machinery (`dispatch`, `_dispatch_async`,
`_consumer`, `_process_batch`) and implement 10 hooks:

```
_get_tracker, _check_tracker_status, _update_tracker, _initial_sync_complete,
_calculate_fetch_cursor, _producer, _publish_records,
_publish_enrichment_messages, _write_batch_size, _consumer_timeout_seconds
```

`_producer` paginates the API and `queue.put(item)`s each (ending with
`DONE_SENTINEL`); the consumer batches and calls `_publish_enrichment_messages`
(to the Enricher SQS queue) then `_publish_records` (base records to Scribe SQS).

**2. Roll your own `dispatch()` (when the crawl shape differs).** GHE-PR
(`historian/biztech_github/ghe_pr_crawler.py`) fans out over repos with a
semaphore + `asyncio.gather` and owns its own event loop; JIRA
(`historian/jira/main.py`) runs a synchronous two-job loop (TCMR + OPERATIONAL)
over a windowed batch tracker with no crawler class. Neither subclasses
`BaseCrawler`, but both return a `CrawlerResult`.

Tracker read/write goes through the **Matik API** (`api/routes/<source>.py`), not
the DB directly. See [Onboarding a Data Source](onboarding-a-data-source.md) for
the full end-to-end walkthrough (models → spec → DAO → client → API route →
historian → scribe → enricher → migration).

---

## Configuration

### YAML Configuration (`matik-historian-config.yaml`)

```yaml
---
common:
  environment: ${ENVIRONMENT}
  log_level: ${LOG_LEVEL}

# API service endpoint for data persistence
api:
  endpoint: http://api:8080

# Connector configurations
connector_conf:
  my_source:
    name: my_source
    enabled: true
    frequency: 60m
    page_size: 100
    cutoff_date: "2023-06-01T00:00:00Z"
    tracker_lookback_days: 30  # For mutable data

# Source-specific configuration
my_source:
  api_key: ${MY_SOURCE_API_KEY}
  base_url: ${MY_SOURCE_BASE_URL}
  items:
    - item1
    - item2
```

### ConnectorConfig Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Connector identifier |
| `enabled` | `bool` | Whether connector is active |
| `frequency` | `str` | Duration at which the connector runs (e.g., "60m" for 60 minutes) |
| `page_size` | `int` | Records per API page (default: 100) |
| `cutoff_date` | `str` | ISO date for initial historical fetch |
| `tracker_lookback_days` | `int` | Days to look back for mutable data |

---

## Configuration Merging Pattern

Historians use a two-tier configuration system that separates common settings from datasource-specific settings.

### Why Merge Configs?

- **Separation of concerns**: Common settings shared across all historians vs. datasource-specific credentials
- **Reduced duplication**: API endpoints, telescope settings, etc. defined once
- **Security**: Sensitive credentials isolated in datasource-specific files
- **Flexibility**: Override specific settings without duplicating entire config

### Implementation

The merge now happens **inside `run_historian`** (the shared shell) — a source does
not call `load_config`/`merge_config` in its `main.py`. The source only declares the
ordered file list; `run_historian` loads the base config and merges each in turn,
returning `1` with the given message on any merge failure:

```python
run_historian(
    spec=get_source("my_source"),
    caller_name=__name__,
    source_config_files=[
        ("matik-historian-my-source-config.yml", "Failed to load config"),
        ("metrics.yml", "Failed to load metrics config"),
        ("matik-enricher-config.yml", "Failed to load enricher config"),
    ],
    build_and_run=_build_and_run,
)
```

Inside `build_and_run`, the merged config arrives on `ctx.config`, and the source's
own section is `ctx.source_config` (`getattr(config, source_config_attr or
spec.source_type)`).

### How Merging Works

The `merge_config` function in `common/config.py`:
1. Reads the additional YAML file
2. Substitutes environment variables (e.g., `${VAR_NAME}`)
3. Merges using dict unpacking: `{**base_dict, **additional_data}`
4. Returns a validated `MatikConfig` object

**Important**: Additional config values **override** base config values for matching keys.

---

## Separate Config Files per Datasource

Each historian datasource should have its own configuration file in `_infra/kube/files/`.

### File Structure

```
local-configs/
├── matik-historian-config.yml               # Base config (shared)
├── matik-historian-incidentio-config.yml    # Incident.io
├── matik-historian-biztech-github-config.yml # GitHub Enterprise
├── matik-historian-jira-config.yml          # JIRA
├── matik-api-config.yml                     # API service
├── metrics.yml                              # Telescope metrics (shared)
└── matik-enricher-config.yml                # Enricher SQS config (REQUIRED — historians hard-fail without it)
```

> Config files use the `.yml` extension. There is no `facade-config.yml` for
> historians anymore — the historian no longer calls the LLM directly; it requires
> `matik-enricher-config.yml` and forwards enrichment to the Enricher.

### Naming Convention

- Base: `matik-historian-config.yaml`
- Datasource-specific: `matik-historian-{datasource-name}-config.yaml`

### What Goes Where

**Base Config (`matik-historian-config.yaml`):**
```yaml
common:
  environment: {{ .Env.Name }}
  log_level: INFO

```

**Datasource-Specific Config (`matik-historian-my-source-config.yaml`):**
```yaml
# Override/add connector_conf for this datasource
connector_conf:
  my_source:
    name: "my_source"
    enabled: true
    frequency: 60m
    page_size: 100
    cutoff_date: "2023-06-01T00:00:00Z"
    tracker_lookback_days: 30

# Datasource-specific settings
my_source:
  api_key: '{{ .App.Secrets.my_source.api_key }}'
  base_url: {{ .Env.Params.my_source.base_url }}
  # ... other datasource-specific settings
```

### Kubernetes App Configuration

Each datasource app mounts both config files:

```yaml
fileMounts:
  - filename: matik-historian-config.yml       # base
  - filename: matik-historian-my-source-config.yml  # per-source
  - filename: metrics.yml                       # Telescope
  - filename: matik-enricher-config.yml         # REQUIRED — enricher SQS config
```

The exact file list a source merges is whatever it passes in
`run_historian(..., source_config_files=[...])`.

---

## Docker Compose Testing

### Starting the Stack

```bash
cd matik/

# Start MySQL and run migrations
docker compose up -d mysql
docker compose up migrator

# Start the API service
docker compose up -d api
```

### Running a Historian Locally

Historians use the `historian` profile and run as one-off jobs:

```bash
# Run GHE historian
docker compose --profile historian run historian_biztech_github

# Run JIRA historian
docker compose --profile historian run historian_jira

# Run with custom environment
docker compose --profile historian run -e LOG_LEVEL=DEBUG historian_biztech_github
```

### Docker Compose Service Definition

Add your historian to `docker-compose.yml`:

```yaml
historian_my_source:
  build:
    context: .
    dockerfile: Dockerfile
  container_name: matik-historian-my-source
  command: ["python", "-m", "historian.my_source"]
  restart: "no"
  profiles:
    - historian
  environment:
    - SERVICE_NAME=historian_my_source
  volumes:
    - ./local-configs:/config:ro
    - ./.env.local:/app/.env:ro
  depends_on:
    mysql:
      condition: service_healthy
    migrator:
      condition: service_completed_successfully
  networks:
    - matik-network
```

### Verifying Data

```bash
# Access Adminer (database UI)
open http://localhost:8081

# Or use MySQL CLI
docker compose exec mysql mysql -u matik_user -p matik_password
```

---

## Checklist for New Historian

> For the full end-to-end checklist (models, spec, DAO, client, API route,
> historian, scribe, enricher, migration, config), follow
> [Onboarding a Data Source](onboarding-a-data-source.md). The historian-specific
> steps are summarized below.

### 1. Create Domain Models (`common/models/`)

- [ ] Create SQLModel for main entity (e.g., `my_record.py`)
- [ ] Create SQLModel for tracker if using lookback (e.g., `my_tracker.py`)
- [ ] Add models to `common/models/__init__.py`
- [ ] Write unit tests for models

### 2. Create Database Schema (`migrator/alembic/versions/`)

- [ ] Create Alembic migration for new tables
- [ ] Run migration locally: `docker compose up migrator`

### 3. Create DAOs (`common/daos/`)

- [ ] Create DAO for main entity with CRUD operations
- [ ] Create DAO for tracker if needed
- [ ] Add DAOs to `common/daos/__init__.py`
- [ ] Write unit tests for DAOs

### 4. Create API Endpoints (`api/`)

- [ ] Add routes for the crawler's **tracker** read/write (`api/routes/<source>.py`
      + `include_router` in `api/main.py`). (Base records are written by Scribe via
      SQS, not the API; there is no historian-side hash-cache route — the Enricher
      owns change detection.)

### 5. Create External Client (`common/clients/`)

- [ ] Create client for external API
- [ ] Add factory function `create_*_client()`
- [ ] Add to `common/clients/__init__.py`
- [ ] Write unit tests

### 6. Create Configuration (`common/models/`)

- [ ] Create config model (e.g., `my_source_config.py`)
- [ ] Add to `MatikConfig` in `matik_config.py`
- [ ] Update `local-configs/matik-historian-config.yaml`
- [ ] Document environment variables in `.env.example`

### 7. Create Configuration Files (`_infra/kube/files/`)

- [ ] Add connector_conf entry to `_infra/kube/files/matik-historian-config.yaml`
- [ ] Create datasource-specific config file `_infra/kube/files/matik-historian-{source}-config.yaml`
- [ ] Add environment params to `_infra/kube/envs/` files

### 8. Create Historian Service (`historian/<source_name>/`)

- [ ] Create package directory with `__init__.py`
- [ ] Create `__main__.py` entry point (two lines)
- [ ] Create `main.py` as a `_build_and_run(ctx)` callback + one `run_historian(...)` call
- [ ] Create `<source>_crawler.py` — subclass `BaseCrawler` (streaming) or expose your own `dispatch() -> CrawlerResult`
- [ ] Write unit tests for crawler

### 9. Setup cronjob or scheduler (`_infra/kube/apps/`)

- [ ] Add the cron frequency to run the historian in the scheduler configuration

### 10. Add Docker Compose Service

- [ ] Add service definition to `docker-compose.yml`
- [ ] Test with `docker compose --profile historian run historian_<source>`

### 11. Documentation

- [ ] Add environment variables to `.env.example`
- [ ] Update configuration documentation if needed
