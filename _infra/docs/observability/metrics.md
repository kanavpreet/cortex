# Matik Metrics Reference

This document lists all OpenTelemetry metrics emitted by Matik services.

## Overview

Matik uses OpenTelemetry for metrics collection, exported via OTLP HTTP to a local otel-collector sidecar. Metrics are organized into the following categories:

- **Client Metrics** - External API request instrumentation
- **Database Metrics** - Database query instrumentation
- **HTTP Metrics** - HTTP request instrumentation (FastAPI)
- **Job Metrics** - Background job instrumentation
- **Facade Metrics** - LLM/Facade API instrumentation
- **SQS Metrics** - SQS worker message processing instrumentation (consumers)
- **SQS Publisher Metrics** - SQS message publish instrumentation (producers/historians)
- **Scribe Metrics** - Scribe service message processing instrumentation
- **Correlation Metrics** - Enigmatologist correlation run instrumentation
- **GHE API Metrics** - GitHub Enterprise API operations
- **GHE Cache Metrics** - GHE PR description caching
- **IncidentIO API Metrics** - IncidentIO API batch operations

---

## Client Metrics

Tracks external API requests (e.g., Incident.io, JIRA, Facade).

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_client_requests_total` | Counter | `{request}` | Total number of external API requests |
| `matik_client_request_duration_seconds` | Histogram | `s` | External API request duration in seconds |
| `matik_client_request_errors_total` | Counter | `{error}` | Total number of external API request errors |
| `matik_client_retries_total` | Counter | `{retry}` | Total number of external API request retries |

**Labels:**
- `service` - Service name (e.g., "historian")
- `client` - Client name (e.g., "incidentio", "facade", "jira")
- `method` - HTTP method (GET, POST, etc.)
- `endpoint` - API endpoint path
- `status` - "success" or "error"
- `status_code` - HTTP status code
- `attempt` - Retry attempt number (retries only)

**Histogram Buckets:** 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0 seconds

---

## Database Metrics

Tracks database query performance and connection pool statistics.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_db_queries_total` | Counter | `{query}` | Total number of database queries |
| `matik_db_query_duration_seconds` | Histogram | `s` | Database query duration in seconds |
| `matik_db_query_errors_total` | Counter | `{error}` | Total number of database query errors |
| `matik_db_pool_open_connections` | Gauge | `{connection}` | Number of open database connections |
| `matik_db_pool_idle_connections` | Gauge | `{connection}` | Number of idle database connections |
| `matik_db_pool_in_use_connections` | Gauge | `{connection}` | Number of database connections currently in use |
| `matik_db_pool_wait_total` | Counter | `{wait}` | Total number of times a connection was waited for |
| `matik_db_pool_wait_duration_seconds` | Histogram | `s` | Time spent waiting for a database connection |

**Labels:**
- `service` - Service name
- `operation` - Query operation (select, insert, update, delete, upsert)
- `table` - Table name being queried
- `status` - "success" or "error"

**Histogram Buckets:** 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0 seconds

---

## HTTP Metrics

Tracks HTTP requests handled by FastAPI services.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_http_requests_total` | Counter | `{request}` | Total number of HTTP requests |
| `matik_http_request_duration_seconds` | Histogram | `s` | HTTP request duration in seconds |
| `matik_http_active_requests` | UpDownCounter | `{request}` | Number of HTTP requests currently being processed |

**Labels:**
- `service` - Service name (e.g., "api")
- `method` - HTTP method (GET, POST, etc.)
- `path` - Route path template (parameterized to avoid high cardinality)
- `status` - HTTP status code
- `status_class` - Status class (2xx, 3xx, 4xx, 5xx)

**Histogram Buckets:** 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 seconds

---

## Job Metrics

Tracks background job execution (e.g., historian crawlers).

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_job_executions_total` | Counter | `{execution}` | Total number of background job executions |
| `matik_job_duration_seconds` | Histogram | `s` | Background job duration in seconds |
| `matik_job_errors_total` | Counter | `{error}` | Total number of background job errors (job failures) |
| `matik_job_errors_encountered_total` | Counter | `{error}` | Total number of errors encountered during job execution (job may still succeed) |
| `matik_job_items_processed_total` | Counter | `{item}` | Total number of items processed by background jobs |
| `matik_job_last_run_timestamp` | Gauge | `s` | Unix timestamp of the last job run |

**Labels:**
- `service` - Service name (e.g., "historian")
- `connector_type` - Job/connector type (e.g., "incidentio_incidents", "ghe_prs")
- `status` - "success" or "error"
- `error_type` - Error type identifier (errors_encountered only, e.g., "rate_limit", "api_error")

**Histogram Buckets:** 1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600 seconds

---

## Facade Metrics

Tracks Facade/LLM API calls and token usage.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_facade_calls_total` | Counter | `{call}` | Total number of Facade/LLM API calls |
| `matik_facade_call_duration_seconds` | Histogram | `s` | Facade/LLM API call duration in seconds |
| `matik_facade_prompt_tokens_total` | Counter | `{token}` | Total prompt tokens used in Facade/LLM calls |
| `matik_facade_completion_tokens_total` | Counter | `{token}` | Total completion tokens used in Facade/LLM calls |
| `matik_facade_call_errors_total` | Counter | `{error}` | Total number of Facade/LLM API call errors |
| `matik_facade_ratelimit_limit_requests` | Gauge | `{request}` | Facade rate limit: max requests allowed |
| `matik_facade_ratelimit_remaining_requests` | Gauge | `{request}` | Facade rate limit: remaining requests |
| `matik_facade_ratelimit_limit_tokens` | Gauge | `{token}` | Facade rate limit: max tokens allowed |
| `matik_facade_ratelimit_remaining_tokens` | Gauge | `{token}` | Facade rate limit: remaining tokens |

**Labels:**
- `service` - Full service name (e.g., "historian-jira", "historian-incidentio", "historian-ghe")
- `client` - LLM client type (e.g., "facade", "bedrock")
- `model` - Model used (e.g., "gpt-4o")
- `operation` - Operation type (e.g., "summarize_root_cause", "summarize_resolution")

**Histogram Buckets:** 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0 seconds

---

## SQS Metrics

Tracks SQS message processing for **consumer/worker** services (e.g., enigmatologist, scribe).

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_sqs_poll_total` | Counter | `{poll}` | Total number of SQS poll attempts; fires on every cycle to keep the series alive during idle periods |
| `matik_sqs_processed_messages_total` | Counter | `{message}` | Total number of SQS messages successfully processed and deleted |
| `matik_sqs_failed_messages_total` | Counter | `{message}` | Total number of SQS messages that failed and were returned to queue |
| `matik_sqs_message_retries_total` | Counter | `{message}` | Total number of redelivered messages (`ApproximateReceiveCount` > 1); the native SQS redrive/backoff signal |
| `matik_sqs_processing_seconds` | Histogram | `s` | SQS message processing duration in seconds |

**Labels:**
- `service` - Service name (e.g., "enigmatologist")
- `queue_name` - SQS queue name (e.g., "matik-enig-triggers-sandbox-queue") - all metrics
- `status` - "success" or "failed" - duration histogram and processed/failed counters
- `error_type` - Exception class name (e.g., "RuntimeError"), "unknown" if none - failed counter only

**Histogram Buckets:** 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0 seconds

---

## SQS Publisher Metrics

Tracks SQS message publishing for **producer** services (historians: incidentio, jira, biztech_github).

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_sqs_messages_published_total` | Counter | `{message}` | Total number of SQS messages successfully published |
| `matik_sqs_messages_publish_failed_total` | Counter | `{message}` | Total number of SQS messages that failed to publish |
| `matik_sqs_publish_duration_seconds` | Histogram | `s` | SQS message publish duration in seconds |

**Labels:**
- `service` - Service name (e.g., "historian-jira", "historian-incidentio", "historian-ghe")
- `queue_name` - SQS queue name derived from the queue URL (e.g., "matik-scribe-sandbox-queue") - all metrics
- `message_type` - Pydantic model class name of the published message (e.g., "JiraBaseMessage", "IncidentIOBaseMessage") - all metrics
- `status` - "success" or "failed" - all metrics
- `error_type` - Exception class name (e.g., "RuntimeError"), "unknown" if none - failed counter only

**Histogram Buckets:** 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0 seconds

**Useful PromQL queries:**

```promql
# Publish rate by service and message type
rate(matik_sqs_messages_published_total[5m])

# Publish error rate
rate(matik_sqs_messages_publish_failed_total[5m])

# Publish success ratio per service
sum(rate(matik_sqs_messages_published_total{status="success"}[5m])) by (service)
/
sum(rate(matik_sqs_messages_published_total[5m])) by (service)

# 95th percentile publish latency by service
histogram_quantile(0.95, sum(rate(matik_sqs_publish_duration_seconds_bucket[5m])) by (le, service))

# Failed publishes by error type
sum(rate(matik_sqs_messages_publish_failed_total[5m])) by (service, error_type)
```

---

## Scribe Metrics

Tracks message processing in the Scribe service, which consumes from SQS and writes to the database.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_scribe_messages_processed_total` | Counter | `{message}` | Total number of Scribe messages processed |
| `matik_scribe_message_processing_duration_seconds` | Histogram | `s` | Time to process a single Scribe message end-to-end |
| `matik_scribe_dlq_messages_total` | Counter | `{message}` | Total number of messages routed to the dead letter queue |
| `matik_scribe_backoff_total` | Counter | `{retry}` | Total number of exponential backoff retries triggered |
| `matik_scribe_queue_depth` | Gauge | `{message}` | Approximate number of messages currently in the queue |

**Labels:**
- `source_type` - Data source (e.g., "incidentio", "ghe_pr", "jira", "correlation") - processed, duration, dlq, backoff
- `message_type` - Message type (e.g., "base", "enrichment") - processed and duration only
- `status` - Outcome (e.g., "success", "error") - processed and duration only
- `error_type` - Exception class name (e.g., "MalformedMessageError") - dlq only
- `queue` - Short queue identifier (e.g., "scribe", "scribe-dlq") - queue_depth only

**Histogram Buckets:** 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0 seconds

---

## Correlation Metrics

Tracks enigmatologist correlation run outcomes and match quality.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_correlation_candidate_events` | Histogram | `{event}` | Number of change events evaluated as candidates per correlation run |
| `matik_correlation_run_matches` | Histogram | `{match}` | Number of matches found per correlation run |
| `matik_correlation_score` | Histogram | `1` | Distribution of final correlation scores across matches |
| `matik_correlation_outcomes_total` | Counter | `{run}` | Correlation run outcomes by path: service_only, llm_only, both, none |
| `matik_correlation_duration_seconds` | Histogram | `s` | End-to-end duration of a single correlation run |
| `matik_correlation_node_duration_seconds` | Histogram | `s` | Duration of an individual LangGraph pipeline node |

**Labels:**
- `service` - Service name (e.g., "enigmatologist") - all metrics
- `type` - Correlation workflow type (e.g., "reliability") - all metrics
- `anchor` - Anchor entity type (e.g., "incident") - all metrics
- `source` - Change event source: "biztech_github" or "jira" - candidate_events only
- `outcome` - Correlation path: "service_only", "llm_only", "both", "none" (also "error"/"unknown" on duration) - outcomes_total and duration_seconds
- `correlation_type` - Match type: "SERVICE_MATCH" or "LLM" - score only
- `node` - LangGraph node name: "fetch_jira_issues", "fetch_biztech_github_prs", "assign_correlations_by_llm" - node_duration_seconds only

**Histogram Buckets (candidates/matches):** 0, 1, 2, 5, 10, 20, 50, 100

**Histogram Buckets (score):** 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0

**Histogram Buckets (run duration):** 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0 seconds

**Histogram Buckets (node duration):** 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0 seconds

---

## GHE API Metrics

Tracks GitHub Enterprise API operations for the API service.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_ghe_api_operations_total` | Counter | `{operation}` | Total number of GHE API operations |
| `matik_ghe_api_operation_duration_seconds` | Histogram | `s` | GHE API operation duration in seconds |
| `matik_ghe_api_batch_size` | Histogram | `{item}` | Batch size distribution for GHE API batch operations |
| `matik_ghe_api_entities_affected_total` | Counter | `{entity}` | Total number of entities affected by GHE API operations |

**Labels:**
- `service` - Service name (e.g., "api")
- `operation` - Operation type (e.g., "upsert", "get", "batch_upsert")
- `entity_type` - Entity type (e.g., "organization", "repository", "pull_request")
- `status` - "success" or "error"

**Histogram Buckets (duration):** 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 seconds

**Histogram Buckets (batch_size):** 1, 5, 10, 25, 50, 100, 250, 500, 1000

---

## GHE Crawler Metrics

Tracks GitHub rate-limit events and per-run statistics for the GHE PR crawler.

> **Note:** Hash-based PR description dedup moved to the Enricher service. Cache
> hit/miss accounting is now reported by `matik_enricher_enrichment_cache_operations_total`
> (see [Enricher Cache Metrics](#enricher-cache-metrics)).

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_ghe_rate_limit_events_total` | Counter | `{event}` | GitHub rate limit events encountered |
| `matik_ghe_crawler_orgs_processed_total` | Counter | `{org}` | Organizations processed by GHE crawler |
| `matik_ghe_crawler_repos_processed_total` | Counter | `{repo}` | Repositories processed by GHE crawler |
| `matik_ghe_crawler_prs_upserted_total` | Counter | `{pr}` | PRs upserted to database by GHE crawler |
| `matik_ghe_crawler_api_calls_total` | Counter | `{call}` | API calls made by GHE crawler |

**Labels:**
- `service` - Service name (e.g., "historian")
- `operation` - Operation that hit rate limit - rate_limit_events only
- `connector_type` - Connector type (e.g., "ghe_pr") - crawler stats only
- `target` - API target ("ghe", "matik") - api_calls only

---

## Enricher Cache Metrics

Tracks hash-based dedup outcomes in the Enricher — how often expensive Facade/LLM
enrichment calls are skipped because an entity's content is unchanged. This
replaces the former per-historian cache metrics (GHE, Jira) now that hash dedup
is centralized in the Enricher.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_enricher_enrichment_cache_operations_total` | Counter | `{operation}` | Enrichment hash-dedup outcomes by result |

**Labels:**
- `service` - Service name (e.g., "enricher")
- `source_type` - Data source ("ghe_pr", "jira", "incidentio")
- `enrichment_type` - Output field being enriched (e.g., "pull_request_summary", "issue_summary")
- `result` - Dedup outcome: "hit" (content unchanged, Facade call skipped), "miss" (content changed), "new" (first seen)

---

## IncidentIO API Metrics

Tracks IncidentIO API batch operations for the API service.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_incidentio_api_batch_size` | Histogram | `{item}` | Batch size distribution for IncidentIO API operations |
| `matik_incidentio_api_entities_affected_total` | Counter | `{entity}` | Total number of entities affected by IncidentIO API operations |

**Labels:**
- `service` - Service name (e.g., "api")
- `entity_type` - Entity type (e.g., "incident", "tracker")

**Histogram Buckets (batch_size):** 1, 5, 10, 25, 50, 100, 250, 500, 1000

---

## MCP Server Metrics

Tracks MCP protocol operations for the `matik-mcp-server` service. These are transport-level metrics emitted by the MCP server — not the Matik API. The API's HTTP layer is tracked separately via HTTP Metrics above.

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `matik_mcp_tool_calls_total` | Counter | `{call}` | Total number of MCP tool calls |
| `matik_mcp_tool_call_duration_seconds` | Histogram | `s` | End-to-end tool call duration (MCP protocol → proxy → API response) |
| `matik_mcp_tool_call_errors_total` | Counter | `{error}` | Total number of MCP tool call errors |
| `matik_mcp_active_sessions` | Gauge | `{session}` | Current number of active MCP client sessions |
| `matik_mcp_spec_refreshes_total` | Counter | `{refresh}` | Total number of OpenAPI spec refresh attempts |

**Labels:**
- `service` - Service name (`"mcp-server"`) — all metrics
- `tool_name` - MCP tool name from `operationId` (e.g., `"mcp_get_correlation_group"`) — tool call metrics
- `status` - `"success"` or `"error"` for tool calls/duration; `"success"`, `"unchanged"`, or `"error"` for spec refreshes
- `error_type` - Exception class name (e.g., `"HTTPStatusError"`, `"RuntimeError"`) or `"unknown_tool"` if the tool was not found in the registry — `tool_call_errors_total` only

**Histogram Buckets (tool call duration):** 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0 seconds

**Useful PromQL queries:**

```promql
# Tool call rate by tool name
rate(matik_mcp_tool_calls_total[5m])

# Tool error rate by tool name
rate(matik_mcp_tool_call_errors_total[5m])

# P95 tool call latency by tool name
histogram_quantile(0.95, sum(rate(matik_mcp_tool_call_duration_seconds_bucket[5m])) by (le, tool_name))

# Spec refresh error rate
rate(matik_mcp_spec_refreshes_total{status="error"}[5m])
```

**Implementation:** `common/metrics/mcp_metrics.py` — `McpMetrics` class. See [mcp-instrumentation-plan.md](mcp-instrumentation-plan.md) for integration details.
