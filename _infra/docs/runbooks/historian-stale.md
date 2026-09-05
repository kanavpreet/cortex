# Matik Historian Staleness

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alerts: `ghe_historian_stale_production`, `jira_historian_stale_production`, `incidentio_historian_stale_production`
- Symptoms: A historian has not completed a run within its expected cadence; the connector's data in Matik is going stale.

This runbook covers all three staleness alerts — they differ only by connector and cadence:

| Alert | Connector / CronJob | Expected cadence | Fires when last-run age exceeds |
| --- | --- | --- | --- |
| `ghe_historian_stale_production` | `matik-historian-biztech-github` | hourly | 3601s (~1h) |
| `jira_historian_stale_production` | `matik-historian-jira` | hourly | 3601s (~1h) |
| `incidentio_historian_stale_production` | `matik-historian-incidentio` | hourly | 3601s (~1h) |

## Impact

Historians are **K8s CronJobs** that batch-crawl external APIs and write via the Matik API → Scribe (per [ADR 012](../decisions/012-historian-k8s-cronjob-architecture.md)). Staleness means the CronJob has stopped completing successful runs, so the connector's data stops refreshing:

- **GHE stale** → PR / code-review data goes stale; features depending on GHE data degrade.
- **JIRA stale** → ticket (TCMR / operational) data goes stale; Jira correlations degrade.
- **Incident.io stale** → incident data goes stale; incident correlations degrade.

Note a job that **runs but errors** keeps updating its last-run timestamp and will *not* trip this alert — that case is covered by [Historian Job Errors](historian-job-errors.md). Staleness specifically means the job is **not completing** (not scheduled, crash-looping, timing out, or blocked by a wedged prior run). See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] DB access to inspect/reset historian trackers — see [db-management](../operations/db-management.md)
- [ ] Grafana access for the [Matik Historians Dashboard](https://grafana.a.musta.ch/goto/ffpyqmmfi988we?orgId=1)

## Steps

> Steps use the JIRA historian as the example; substitute the alerting connector's app label: `matik-historian-jira-production`, `matik-historian-incidentio-production`, or `matik-historian-biztech-github-production`.

1. Confirm the last-run age for the alerting connector.

   ```promql
   time() - max(matik_job_last_run_timestamp{app=~"matik-historian-jira-.*", deployment_environment="production"})
   ```

2. List recent CronJob runs and their status for the connector.

   ```bash
   kubectl -n matik-production get jobs -l app=matik-historian-jira-production --sort-by=.metadata.creationTimestamp
   ```

3. Check whether the CronJob is suspended or has a stuck (wedged) active job — `concurrencyPolicy: Forbid` means a hung run blocks the next scheduled run.

   ```bash
   kubectl -n matik-production get cronjob matik-historian-jira-production -o wide
   ```

4. Inspect the most recent job's pod logs for crashes, timeouts, or a refusal to run.

   ```bash
   kubectl -n matik-production logs -l app=matik-historian-jira-production --tail=200
   ```

5. For `incidentio` or `jira`, check whether the tracker is stuck in `ERROR` — the crawler **refuses to run** in this state, which presents as staleness.

   ```sql
   SELECT id, status, error_message, last_updated_at_cursor FROM incidentio_tracker WHERE id = 1;
   SELECT ticket_type, status, error_message FROM jira_batch_tracker;
   ```

6. If a prior job is wedged/active and blocking new runs, delete it so the next schedule can start.

   ```bash
   kubectl -n matik-production delete job <stuck-historian-job-name>
   ```

7. If the CronJob is suspended, resume it.

   ```bash
   kubectl -n matik-production patch cronjob matik-historian-jira-production -p '{"spec":{"suspend":false}}'
   ```

8. For `incidentio` / `jira` stuck in `ERROR`, fix the underlying cause then reset the tracker so the next run resumes (see [historian-failure-handling](../operations/historian-failure-handling.md)).

   ```sql
   UPDATE jira_batch_tracker SET status = 'OK' WHERE ticket_type = '<type>';
   -- or, for incidentio:
   UPDATE incidentio_tracker SET status = 'OK' WHERE id = 1;
   ```

9. To validate a fix immediately rather than waiting for the schedule, trigger a manual run from the CronJob.

   ```bash
   kubectl -n matik-production create job --from=cronjob/matik-historian-jira-production historian-jira-manual-$(date +%s)
   ```

10. If the job is timing out on large result sets, tune `activeDeadlineSeconds` for that CronJob.

    TODO: confirm the canonical mechanism to change `activeDeadlineSeconds` / schedule (kube-gen param + redeploy).

## Verify

Confirm the last-run age has dropped below the connector's threshold.

```promql
time() - max(matik_job_last_run_timestamp{app=~"matik-historian-jira-.*", deployment_environment="production"})
```

Expected output: a value below the connector's threshold (3601s for all connectors), refreshing each hour; the alert clears in #matik-alerts.

Confirm a recent run completed successfully and processed items.

```bash
kubectl -n matik-production get jobs -l app=matik-historian-jira-production --sort-by=.metadata.creationTimestamp | tail -3
```

Expected output: a recent job showing `Complete`.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes (or before significant data staleness) | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Upstream API (Incident.io / JIRA / GHE) outage or auth failure | Confirm credentials/secrets; escalate to the provider integration owner |
| Job runs but errors (timestamp updating) | Follow [Historian Job Errors](historian-job-errors.md) |
| Tracker reset does not clear the staleness | Post in #matik-internal with the `error_message` |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

Historians are CronJobs. If runs stopped completing after a release, roll back the historian image/config to the last good version.

TODO: add the canonical historian CronJob rollback procedure (kube-gen revert / Spinnaker pipeline link).

## Appendix

### Alert definition

Defined in `historians/historians_stale.ts` (three Telescope/CAWS monitors). Each computes the age of the most recent run:

```promql
time() - max(matik_job_last_run_timestamp{app=~"matik-historian-<connector>-.*", deployment_environment="production"})
```

- Recording rules: `matik:ghe_historian_last_run_age:max`, `matik:jira_historian_last_run_age:max`, `matik:incidentio_historian_last_run_age:max`
- Metric: `matik_job_last_run_timestamp` (Gauge; unix timestamp of the last job run, see [Job Metrics](../observability/metrics.md#job-metrics)).
- Thresholds / window: all connectors `> 3601`s (hourly cadence); all `for: 1m`.
- Severity: critical; all notify `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Tracker stuck in ERROR (incidentio/jira)** — a prior failure flipped the tracker; the crawler refuses to run until reset, so runs never complete. Confirm via the tracker query (step 5). See [Historian Job Errors](historian-job-errors.md) for the failure that caused it.
- **Wedged prior job** — `concurrencyPolicy: Forbid` blocks new runs while a hung job is active. Confirm via CronJob/job status (step 3) and delete the stuck job.
- **CronJob suspended** — `suspend: true` (manual or accidental) stops scheduling. Confirm via step 3.
- **Crash loop / timeout** — the job fails to start or exceeds `activeDeadlineSeconds`. Confirm via pod logs and job status.
- **Upstream API outage** — the source API is unreachable and the job cannot complete. Confirm via logs.
- **Scheduler / cluster issue** — CronJob not firing at all. Confirm via job creation timestamps.

GHE note: the GHE tracker is write-only and never read back, so GHE staleness is not caused by an ERROR tracker — focus on job scheduling, timeouts, and GHE API health.

### Related docs

- [Historian Failure Handling](../operations/historian-failure-handling.md)
- [ADR 012 — Historian K8s CronJob Architecture](../decisions/012-historian-k8s-cronjob-architecture.md)
- [GHE Historian Crawler](../architecture/ghe_historian_crawler.md) · [JIRA Historian Crawler](../architecture/jira_historian_crawler.md)
- [DB Management](../operations/db-management.md) · [Metrics Reference](../observability/metrics.md#job-metrics)
- Related runbooks: [Historian Job Errors](historian-job-errors.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
