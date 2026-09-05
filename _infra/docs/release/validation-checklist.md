# Validation Checklist

Use this checklist to confirm Matik is running correctly before promoting
**sandbox → staging → production**. Validation relies on two signals you have
access to:

- **Logs** — structured JSON logs (via `structlog`) shipped to the
  `logs-shared-beech` cluster. Search by `service_name`, `environment`, and the
  exact log message strings below.
- **Grafana** — the [service-health dashboard](https://grafana.a.musta.ch/goto/ffo8nog9p7ke8d?orgId=1)
  for latency, error rate, DB connections, SQS depth, and DLQ counts, plus the
  [infrastructure dashboard](https://grafana.a.musta.ch/goto/cfo8ztsnrso3kd?orgId=1)
  for cluster/host-level signals. Per-component dashboards are linked in each
  section below.

Each component below lists **what it is**, the **healthy log signals**, the
**Grafana / data signals**, and the **red flags** that block promotion. The
[Promotion Gate Matrix](#promotion-gate-matrix) at the end summarizes the minimum
green signals per environment.

> Log strings are quoted exactly as emitted by each service's `main.py`. Match on
> the message field, scoped to the service and environment.

---

## infrastructure

**What:** cluster and database resource usage for the staging cell. This is not
an application signal — it comes from `kube-state-metrics` and AWS CloudWatch.

**Grafana:** [infrastructure dashboard](https://grafana.a.musta.ch/goto/cfo8ztsnrso3kd?orgId=1)
— pod restarts, OOMKilled containers, RDS CPU, RDS connections, and CPU/memory
usage for services and historians.

**Good state (pass criteria):**

- [ ] Pod restarts (range): 0. A value of 1–4 needs a pod-log check; 5 or more blocks promotion.
- [ ] OOMKilled containers: 0. Any value of 1 or more blocks promotion.
- [ ] RDS CPU: under 60%. At or above 80% blocks promotion.
- [ ] RDS connections: under 100. At or above 200 blocks promotion.
- [ ] CPU / memory usage (services and historians): under 80% of the requested amount. At or above 95% blocks promotion.

**Red flags:** a rising restart count; any OOMKilled container; RDS CPU or
connections trending toward the red threshold; sustained CPU/memory usage
above 80%.

---

## api

**What:** FastAPI service exposing correlation/data endpoints + the MCP routes.
The only component with HTTP health probes.

**HTTP checks** (from `matik/api/routes/health.py`):

| Endpoint | Meaning | Healthy response |
|----------|---------|------------------|
| `GET /health` | Liveness | `200` — `{"status": "healthy", "service": "matik-api"}` |
| `GET /ready` | Readiness (runs `SELECT 1`) | `200` — `{"status": "ready", ..., "checks": {"database": "ok"}}` |

A `503` from `/ready` with `"database": "error: ..."` means the DB is unreachable.

**Healthy log signals:** `database engine initialized` → `api service started`
(plus `telescope metrics initialized` when metrics are on).

**Grafana:** [api dashboard](https://grafana.a.musta.ch/goto/dfo8ze5tuc3r4e?orgId=1)
— request latency and error rate steady; DB connection pool not saturated.

**Good state (pass criteria):** availability at 99.9% or higher (shown as
Healthy); 5xx rate at 0 requests/s (red at 0.01/s or higher); P95 latency
under 0.5s (red at 1s or higher).

**Red flags:** `/ready` returning 503; `Readiness check failed`;
`Database health check failed`; rising 5xx rate; pod restarts.

---

## mcp

**What:** MCP server for Claude integration; fronted by the api. In staging/prod
it uses `CONSISTENT_HASH` load balancing keyed on the authenticated user email.

**Healthy signals:** MCP health route returns success; requests route to pods
without errors. Shares the api's logging/metrics.

**Grafana:** [mcp dashboard](https://grafana.a.musta.ch/goto/bfo8zqh0s8z5sf?orgId=1)
— request latency/errors and routing health across pods.

**Good state (pass criteria):** tool call error rate at 0/s (red at 0.001/s
or higher); P95 tool latency under 0.5s (red at 1s or higher).

**Red flags:** MCP request errors; uneven/failing routing in staging/prod.

---

## chronicler

**What:** long-running Kafka consumer. Reads Yoyo callback topics (GHE / Jira /
Incident.io / the generic Matik webhook — e.g. OpsBot's incident-channel-summary
feed) and publishes sanitized base messages to **Scribe SQS** and optional
enrichment requests to **Enricher SQS**. Daemon — no HTTP.

**Healthy log signals (startup order):**
`Provider transformers verified` → `Scribe publisher initialized` →
(`Enrichment publisher initialized` when configured) → `Chronicler service started`.

**Grafana / data:** [chronicler dashboard](https://grafana.a.musta.ch/goto/cfo8ziewkbtvkb?orgId=1)
— Kafka consumer lag flat or draining on all topics (including
`matik_generic_webhook_events` / `matik_sandbox_generic_webhook_events`); Scribe
queue receiving messages; chronicler processed-message counters advancing.

**Good state (pass criteria):** availability at 99.9% or higher (shown as
Healthy); Kafka error rate at 0/s (red at 0.01/s or higher); P95 processing
time under 0.5s (red at 1s or higher).

**Red flags:** `Enricher queue not configured`; missing
`github_webhook_secret` / `incidentio_webhook_secret` / `generic_webhook_secret`
warnings in **staging/prod** (acceptable only in sandbox bootstrap); `Chronicler
exited with an unhandled error`; growing Kafka lag.

> Each environment uses a distinct consumer group (`matik-chronicler-sandbox` /
> `-staging` / `matik-chronicler`), so offsets are independent.

---

## scribe (high / medium / low)

**What:** the sole database writer. Three deployments, one per priority queue,
differing only by mounted config. **Deployed in sandbox and staging** — validate
it in both; production enablement follows once staging soaks clean. Daemon — no HTTP.

**Healthy log signals:** `starting scribe` → `database connectivity check passed`
→ `scribe starting`. During traffic: `scribe received messages` (with a `count`
field).

**Grafana / data:** [scribe dashboard](https://grafana.a.musta.ch/goto/dfo8zoyhv1h4wa?orgId=1)
— correlation tables (`ghe_pull_requests`, `incidentio_incidents`, `jira_issues`,
`reliability_correlation`) receiving upserts; **DLQ depth ~0**; visibility-timeout
extensions not climbing.

**Good state (pass criteria):** DLQ depth at 0 (red at 1 or higher); DLQ
routing rate at 0/s; processing rate above 0 messages/s.

**Red flags:** `Failed to load scribe config`; `database connectivity check failed`;
`scribe config section missing` / `mysql config section missing`; messages landing
in the DLQ; repeated `SQS poll error, retrying`.

> Backoff on transient errors is `10s / 30s / 60s ± 20% jitter`, then SQS redrive
> to the DLQ after `max_receive_count`.

---

## enricher

**What:** SQS consumer that calls the Facade LLM to enrich base messages (summaries,
etc.) and writes results back. Has explicit backpressure to avoid silent DLQ
drops. Daemon — no HTTP.

**Healthy log signals:** `starting enricher` →
(`Telescope metrics initialized`) → `Enricher service started`. Per message:
`Enrichment message processed successfully`.

**Grafana / data:** [enricher dashboard](https://grafana.a.musta.ch/goto/afo8zv132sa2od?orgId=1)
— enrichment success rate high; enricher queue depth draining; **DLQ depth ~0**;
Facade call error rate low.

**Good state (pass criteria):** DLQ depth at 0 (red at 1 or higher); error
rate at 0/s (red at 0.001/s or higher).

**Red flags:** `enricher config not found` / `enricher config missing required
fields`; `facade config not found`; `Non-retryable error, routing to DLQ`;
`Max receive count reached, SQS will redrive to DLQ`; sustained
`Retryable error, applying backoff`.

---

## enigmatologist

**What:** SQS-based correlation worker. Runs reliability correlation (LLM via
Bedrock or Facade) and persists results via the api. Daemon — no HTTP.

**Healthy log signals:** `starting enigmatologist` →
`llm_provider configured` → (`Telescope metrics initialized`) →
`api configured for persistence` → `SQS polling started`.

**Grafana / data:** [enigmatologist dashboard](https://grafana.a.musta.ch/goto/efo8zrkldv8xse?orgId=1)
— `reliability_correlation` rows being written; correlation queue draining;
correlation success metrics advancing.

**Good state (pass criteria):** DLQ depth at 0 (red at 1 or higher); failure
rate at 0/s (red at 0.001/s or higher); triggers queue depth draining, not
climbing.

**Red flags:** `enigmatologist sqs_queue_url not configured` /
`scribe_queue_url not configured`; `bedrock config is not loaded` /
`facade config is not loaded`; `api config not found, persistence disabled` in
staging/prod; sustained `correlation failed, message will be retried`.

---

## historian (jira / ghe / incidentio)

**What:** three independent **cronjobs** (not daemons) that crawl external sources
and publish base messages to Scribe. Each runs to completion and exits `0`.
Cadence differs by environment (see [`kube-gen.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/kube/kube-gen.yml)).

**Healthy log signals** (incidentio shown; jira/ghe are analogous):
`IncidentIO historian crawler started` → `SQS publisher initialized` →
`IncidentIO historian crawler completed successfully` (with `incidents_processed`).
Job exit code `0`.

**Grafana / data:** [historian dashboard](https://grafana.a.musta.ch/goto/dfo8zlhyp064gb?orgId=1)
— tracker tables advancing (`incidentio_tracker`, `jira_batch_tracker`,
`ghe_pr_tracker`); source tables growing; job-success metric recorded.

**Good state (pass criteria):** time since last run under 3600s (1 hour),
shown as Healthy. A Stale reading (3601s or higher) blocks promotion and
needs an investigation of the historian cronjob.

**Red flags:** `Failed to load config`; `... configuration not found`;
`IncidentIO historian crawler failed`; non-zero exit; cronjob not firing on
schedule; tracker not advancing across runs.

> For deeper triage see [Historian Failure Handling](../operations/historian-failure-handling.md).

---

## migrator

**What:** one-time Alembic job that runs **before** services in every environment
deploy. Brings the schema to `head`.

**Healthy log signals:** `upgrading database to revision` →
`upgrade completed successfully`. Job exit code `0`.

**Verify revision:** `python -m migrator current` reports the expected `head`
revision matching the release.

**Red flags:** `migration failed`; non-zero exit; `current` not at `head` after
the job; services starting against a stale schema.

---

## datastore & queues

**Database (MySQL via ProxySQL):**

- [ ] api `/ready` returns `database: ok`.
- [ ] No `database connectivity check failed` across scribe/enricher/enigmatologist.
- [ ] Schema at the release `head` revision (see [migrator](#migrator)).

**SQS / DLQs:** [SQS dashboard](https://grafana.a.musta.ch/goto/efo8zn7g1181se?orgId=1)
— queue depths, DLQ counts, and visibility-timeout/backoff metrics across all queues.

- [ ] Main queues (scribe high/medium/low, enricher, enigmatologist) draining —
      depth not monotonically rising.
- [ ] **All DLQs at 0** (the same 0/red-at-1 threshold used on the dashboard).
      A DLQ depth of 1 or more is the clearest "something is wrong" signal —
      inspect with the existing `scripts/dlq_inspector` tooling.
- [ ] Visibility-timeout extension / backoff metrics not climbing.

---

## Promotion Gate Matrix

Minimum green signals before promoting **out of** each environment. `—` means the
component is not deployed there.

| Component | Sandbox (before → staging) | Staging (before → prod) | Production (post-deploy) |
|-----------|----------------------------|--------------------------|--------------------------|
| **infrastructure** | 0 restarts, 0 OOMKilled | + RDS CPU < 60%, RDS connections < 100, CPU/mem < 80% over soak | + same signals sustained post-deploy |
| **migrator** | `upgrade completed successfully`, `current` == head | same | same |
| **api** | `/health` 200, `/ready` `database: ok`, `api service started` | + steady latency/errors in Grafana over soak | + steady latency/errors, no 5xx spike |
| **mcp** | health route ok | + routing healthy across cells | + routing healthy across cells |
| **chronicler** | `Chronicler service started`, Kafka lag draining | + webhook secrets set, lag flat over soak | + lag flat |
| **scribe** | `database connectivity check passed`, DLQ ~0, DB upserts seen | + DB upserts across all cells, DLQ ~0 over soak | — (enable after staging soak) |
| **enricher** | `Enricher service started`, DLQ ~0 | + success rate steady, DLQ ~0 over soak | + DLQ ~0 |
| **enigmatologist** | `SQS polling started`, correlations written | + queue draining, DLQ ~0 over soak | + correlations written, DLQ ~0 |
| **historian** | each crawler exits 0, trackers advance | + crawlers green on staging cadence over soak | + crawlers fire on prod cadence, trackers advance |
| **datastore/queues** | DB reachable, DLQs ~0 | + queues draining over soak | + queues draining, DLQs ~0 |
| **whole app** | no errors over short window; integration tests pass | **soak with no restarts/errors**; Grafana clean; no active alerts | no restarts/errors post-deploy; comms sent |

> **scribe** is validated in sandbox and staging; production enablement is the
> next rollout step after staging soaks clean. Staging and production share the
> same three-cell layout (`pug`, `taz`, `yun`); confirm health across **all
> cells**, not just one.
