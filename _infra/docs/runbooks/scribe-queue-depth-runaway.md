# Matik Scribe Queue Depth Runaway

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `scribe_queue_depth_runaway_production`
- Symptoms: A Scribe SQS queue depth has exceeded 5,000 messages; the DB write worker is critically behind.

## Impact

Scribe is the **sole DB writer**, running as three priority-tier deployments (`high`/`medium`/`low`), each consuming one queue and upserting to MySQL (per [scribe design](../architecture/scribe-design.md)). When a queue runs away, the database is **falling behind the real-time pipeline**: write latency grows and the affected data lags. Which tier matters — `matik-scribe-high` backlog delays real-time Chronicler events (most user-visible); `medium` delays enrichment writes; `low` delays historical backfill. The alert regex spans all tiers. Throughput is bounded by `max_concurrent_writes × replicas` and per-write DB latency, so a backlog usually means slow DB writes or a surge exceeding capacity. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to inspect the Scribe SQS queues
- [ ] DB access — see [db-management](../operations/db-management.md)
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/bfpyv37tq5ptsd?orgId=1)

## Steps

1. Identify which queue (tier) is backed up. The `<tier>` (`high`/`medium`/`low`) extracted here is used to fill in `<tier>` in the commands throughout the rest of this runbook.

   ```promql
   max by (queue_name) (aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-scrb-.*production.*", queue_name!~".*dlq.*"})
   ```

2. Confirm the worker is processing (rule out a stalled/dead pod driving the backlog).

   ```promql
   sum(rate(matik_scribe_messages_processed_total{status="success", deployment_environment="production"}[5m]))
   ```

3. Check per-write DB latency — slow writes are the usual throughput limiter.

   ```promql
   histogram_quantile(0.95, sum by (le) (rate(matik_db_query_duration_seconds_bucket{service="scribe", deployment_environment="production"}[5m])))
   ```

4. Check whether write errors/backoffs are throttling drain.

   ```promql
   sum(rate(matik_scribe_backoff_total{deployment_environment="production"}[5m]))
   ```

5. Check the affected tier's pod health, replica count, and resource pressure (`<tier>` from step 1).

   ```bash
   kubectl -n matik-production top pods -l app=matik-scribe-<tier>-production
   kubectl -n matik-production get deploy/matik-scribe-<tier>-production
   ```

6. If the database is slow or writes are failing, follow the [RDS runbooks](infra-rds-cpu-saturation.md).

7. List recent deployments to check whether throughput dropped after a release (`<tier>` from step 1).

   ```bash
   kubectl -n matik-production rollout history deploy/matik-scribe-<tier>-production
   ```

8. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

9. If the worker is healthy but behind a surge (e.g. a large Historian backfill flooding the `low` tier), scale out the affected tier (`<tier>` from step 1). Keep `mysql.max_open_conns >= max_concurrent_writes × replicas` so the pool can serve the added concurrency.

   ```bash
   kubectl -n matik-production scale deploy/matik-scribe-<tier>-production --replicas=3
   ```

   TODO: confirm the canonical scaling mechanism (replicas vs. `max_concurrent_writes` in `matik-scribe-<tier>-config.yml` vs. HPA) and DB-pool-safe values.

## Verify

Confirm the affected queue depth is draining below threshold.

```promql
max by (queue_name) (aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-scrb-.*production.*", queue_name!~".*dlq.*"})
```

Expected output: a steadily falling depth back under `5000` and trending down; the alert clears in #matik-alerts.

Confirm processed throughput exceeds inflow.

```promql
sum(rate(matik_scribe_messages_processed_total{status="success", deployment_environment="production"}[5m]))
```

Expected output: a processed rate high enough to drain the backlog.

## Escalate

| Condition | Action |
| --- | --- |
| Depth still climbing after 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Shared biztech RDS slow/degraded | Follow [RDS runbooks](infra-rds-cpu-saturation.md); escalate to the DB infra owner |
| Write errors driving the backlog | Check DB query metrics and the [RDS runbooks](infra-rds-cpu-saturation.md) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If throughput dropped after a release, roll back the affected Scribe tier (`<tier>` from step 1).

```bash
kubectl -n matik-production rollout undo deploy/matik-scribe-<tier>-production
```

TODO: add the Matik Scribe Spinnaker pipeline links for all three tiers (high/medium/low).

## Appendix

### Alert definition

Defined in `scribe/scribe_queue_depth_runaway.ts` (Telescope/CAWS monitor `scribe_queue_depth_runaway_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-scrb-.*production.*", queue_name!~".*dlq.*"})
```

- Recording rule: `matik:scribe_queue_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; the `dlq` queues are excluded). Spans all three priority-tier queues. Not a Matik OTel metric — works even when Scribe is down.
- Threshold / window: `> 5000` messages, `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Slow DB writes** — high write latency (shared RDS under load, lock contention) caps throughput. Confirm via DB latency p95 (step 3) and the RDS alerts.
- **Throughput below inflow** — a surge (large Historian backfill on the `low` tier, webhook burst on `high`) exceeds `max_concurrent_writes × replicas`. Confirm via inflow vs. processed rate.
- **Write errors / backoffs** — failing writes repeatedly defer messages. Confirm via the backoff rate (step 4) and DB query metrics.
- **Worker dead / reduced capacity** — fewer healthy pods draining the queue. Confirm via pod status and processed rate.
- **Bad recent deployment** — a regression slowed processing. Confirm via rollout history.

### Related docs

- [Scribe Design](../architecture/scribe-design.md)
- [DB Management](../operations/db-management.md) · [Metrics Reference](../observability/metrics.md)
- Related runbooks: [DLQ Non-Empty](scribe-dlq-non-empty.md), [RDS CPU Saturation](infra-rds-cpu-saturation.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
