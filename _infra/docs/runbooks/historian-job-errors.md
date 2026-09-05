# Matik Historian Job Errors

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `historian_job_errors_production`
- Symptoms: A historian job runs but is failing to ingest data for a connector (`connector_type` label identifies which).

## Impact

Historians are **K8s CronJobs** (one per source: `incidentio`, `jira`, `biztech-github`) that batch-crawl external APIs and write via the Matik API → Scribe, also queuing enrichment (per [ADR 012](../decisions/012-historian-k8s-cronjob-architecture.md)). This alert is the **silent-staleness catch**: a job that runs but errors keeps updating its last-run timestamp, so the [staleness alerts](historian-stale.md) will *not* fire — the connector's data goes stale unnoticed. Each connector depends on its upstream API plus `matik-api-production` and `llm-fusion-hub-production`. For `incidentio` and `jira`, a failure also flips the tracker to `ERROR` and the **next run refuses to start until manually reset** (per [historian-failure-handling](../operations/historian-failure-handling.md)). See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] DB access to inspect/reset historian trackers — see [db-management](../operations/db-management.md)
- [ ] Grafana access for the [Matik Historians Dashboard](https://grafana.a.musta.ch/goto/ffpyqmmfi988we?orgId=1)

## Steps

1. Identify the failing connector from the alert's `connector_type` label.

   ```promql
   sum by (connector_type) (rate(matik_job_errors_total{deployment_environment="production"}[5m]))
   ```

2. Map the connector to its CronJob (`incidentio` → `matik-historian-incidentio`, `jira` → `matik-historian-jira`, `ghe_prs` → `matik-historian-biztech-github`) and list recent job runs.

   ```bash
   kubectl -n matik-production get jobs -l app=matik-historian-incidentio-production --sort-by=.metadata.creationTimestamp
   ```

3. Inspect the most recent failing job's pod logs for the error.

   ```bash
   kubectl -n matik-production logs -l app=matik-historian-incidentio-production --tail=200
   ```

4. Check the errors-encountered breakdown by type to classify (rate limit vs. API error vs. other).

   ```promql
   sum by (connector_type, error_type) (rate(matik_job_errors_encountered_total{deployment_environment="production"}[5m]))
   ```

5. Verify the upstream API for the connector is reachable (Incident.io / JIRA / GitHub Enterprise) from the log detail.

6. Verify the Matik API dependency is healthy (historians write through it).

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
   ```

7. For `incidentio` or `jira`, check whether the tracker is stuck in `ERROR` (these refuse to run until reset).

   ```sql
   SELECT id, status, error_message, last_updated_at_cursor FROM incidentio_tracker WHERE id = 1;
   SELECT ticket_type, status, error_message FROM jira_batch_tracker;
   ```

8. List recent deployments to check whether errors started after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-api-production
   ```

   TODO: confirm how historian CronJob image/config versions are rolled (kube-gen / Spinnaker) to correlate a job regression.

9. Fix the root cause (upstream API, credentials, Matik API, or a code regression). If a deploy is implicated, roll it back (see [Rollback](#rollback)).

10. For `incidentio` / `jira`, after fixing the cause, reset the tracker so the next scheduled run resumes. See [historian-failure-handling](../operations/historian-failure-handling.md).

    ```sql
    UPDATE incidentio_tracker SET status = 'OK' WHERE id = 1;
    -- or, for jira (per ticket type):
    UPDATE jira_batch_tracker SET status = 'OK' WHERE ticket_type = '<type>';
    ```

11. For `ghe_prs`, no tracker reset is needed — the next run recalculates the lookback window automatically.

## Verify

Confirm the job error rate for the connector has returned to zero.

```promql
sum by (connector_type) (rate(matik_job_errors_total{deployment_environment="production"}[5m]))
```

Expected output: `0` for the affected `connector_type` across subsequent scheduled runs; the alert clears in #matik-alerts.

Confirm a recent job succeeded and items were processed.

```promql
sum by (connector_type) (increase(matik_job_items_processed_total{deployment_environment="production"}[15m]))
```

Expected output: a non-zero processed count for the connector after its next run.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Upstream API (Incident.io / JIRA / GHE) outage or auth failure | Confirm credentials/secrets; escalate to the provider integration owner |
| `matik-api` unhealthy | See [API runbooks](api-availability-low.md); coordinate with Ops Eng |
| Tracker reset does not clear the error | Post in #matik-internal with the `error_message` |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

Historians are CronJobs. If errors began after a release, roll back the historian image/config to the last good version.

TODO: add the canonical historian CronJob rollback procedure (kube-gen revert / Spinnaker pipeline link). Note `concurrencyPolicy: Forbid` means a wedged in-flight job can block the next run — delete a stuck job if needed:

```bash
kubectl -n matik-production delete job <stuck-historian-job-name>
```

## Appendix

### Alert definition

Defined in `historians/historians_job_errors.ts` (Telescope/CAWS monitor `historian_job_errors_production`):

```promql
sum by (connector_type) (rate(matik_job_errors_total{deployment_environment="production"}[5m]))
```

- Recording rule: `matik:historian_job_errors:sum_by_connector`
- Metric: `matik_job_errors_total` (Counter; job failures, dimensioned by `connector_type`, see [Job Metrics](../observability/metrics.md#job-metrics)). Related: `matik_job_errors_encountered_total` (errors during a run that may still succeed) and `matik_job_last_run_timestamp` (drives the staleness alerts).
- Threshold / window: `> 0` errors/s, `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Upstream API failure** — the source API (Incident.io / JIRA / GHE) is erroring, rate-limiting, or rejecting auth. Confirm via `error_type` (step 4) and pod logs.
- **Matik API failure** — historians write through `matik-api-production`; API 5xx/timeouts fail the job. Confirm via `/ready`.
- **Tracker stuck in ERROR (incidentio/jira)** — a prior failure flipped the tracker; subsequent runs refuse to start. Confirm via the tracker query (step 7).
- **Bad recent deployment** — a regression in the crawler. Confirm by correlating with rollout history.
- **Job timeout** — the run exceeds `activeDeadlineSeconds` for large result sets. Confirm via job status (terminated/DeadlineExceeded) and tune the CronJob deadline.

### Related docs

- [Historian Failure Handling](../operations/historian-failure-handling.md)
- [ADR 012 — Historian K8s CronJob Architecture](../decisions/012-historian-k8s-cronjob-architecture.md)
- [GHE Historian Crawler](../architecture/ghe_historian_crawler.md) · [JIRA Historian Crawler](../architecture/jira_historian_crawler.md)
- [DB Management](../operations/db-management.md) · [Metrics Reference](../observability/metrics.md#job-metrics)
- Related runbooks: [Historian Staleness](historian-stale.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
