# Biztech RDS InnoDB Deadlocks

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `rds_innodb_deadlocks_production`
- Symptoms: InnoDB deadlocks detected on the biztech RDS master; transaction contention may cascade to write failures.

## Impact

This alerts on the **shared biztech RDS cluster master** — Matik does **not** own this database. Persistent InnoDB deadlocks cause write failures and transaction rollbacks for **all** biztech services sharing the cluster, Matik included. For Matik, rolled-back transactions surface as `matik-api` write errors (5xx) and failed Scribe persistence. Because writes are concentrated on the master, this is a write-path event. A small number of deadlocks is normal under concurrency (the loser's transaction rolls back and retries); this alert fires on a sustained non-zero floor, indicating real contention. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)
- [ ] DB access / contact for the shared biztech RDS cluster — see [db-management](../operations/db-management.md)
- [ ] Escalation contact for the biztech RDS / database infra owner

## Steps

1. Confirm the sustained deadlock signal on the master.

   ```promql
   min_over_time(avg by (dbinstanceidentifier, db_role) (mysql_innodb_metrics_lock_deadlocks:avg_without_instance{db_cluster="biztech", db_env=~"production|internal", db_role=~"master|replica"})[15m:])
   ```

2. Check whether Matik writes are failing or rolling back.

   ```bash
   kubectl -n matik-production logs -l app=matik-api-production --tail=200 | grep -iE "deadlock|rollback|lock wait|1213|1205"
   ```

3. Identify the conflicting transactions/statements via the cluster's `SHOW ENGINE INNODB STATUS` / slow query logs (latest detected deadlock).

   TODO: add the canonical way to read InnoDB deadlock detail on the biztech cluster (console / `#` channel / who can run `SHOW ENGINE INNODB STATUS`).

4. Determine whether a Matik write path is involved (e.g. concurrent Scribe upserts to the same rows, or a historian backfill colliding with real-time chronicler writes on the same tables).

5. Check for a recent Matik change that altered write patterns, transaction scope, or added a hot-row update.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-api-production
   ```

6. If a Matik backfill is colliding with real-time writes, reduce concurrency by suspending the offending historian CronJob until contention clears.

   ```bash
   kubectl -n matik-production patch cronjob <matik-historian-job> -p '{"spec":{"suspend":true}}'
   ```

   Remember to un-suspend it after the incident.

7. If a recent Matik deploy introduced the contention, roll it back (see [Rollback](#rollback)).

8. If the conflicting transactions are **not** Matik's, or the fix requires DB-level changes, escalate to the biztech RDS owner (see [Escalate](#escalate)).

## Verify

Confirm deadlocks have subsided.

```promql
min_over_time(avg by (dbinstanceidentifier, db_role) (mysql_innodb_metrics_lock_deadlocks:avg_without_instance{db_cluster="biztech", db_env=~"production|internal", db_role=~"master|replica"})[15m:])
```

Expected output: value back at `0`; the alert clears in #matik-alerts.

Confirm Matik writes succeed without rollbacks.

```bash
kubectl -n matik-production logs -l app=matik-api-production --tail=100 | grep -iE "deadlock|rollback"
```

Expected output: no new deadlock/rollback log lines.

## Escalate

| Condition | Action |
| --- | --- |
| Conflicting transactions are not Matik's, or a DB-level fix is needed | Escalate to the biztech RDS / database infra owner (TODO: confirm team/channel) |
| Matik API writes failing | See [API runbooks](api-availability-low.md) |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO) and the DB infra on-call |
| Root cause unclear after initial triage | Post in #matik-internal with the deadlock detail |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.
- Biztech RDS / DB infra owner: TODO — confirm team and escalation channel.

## Rollback

If a Matik release introduced the contention (new transaction scope, hot-row update, or changed write order), roll back the offending service.

```bash
kubectl -n matik-production rollout undo deploy/matik-api-production
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_rds_deadlocks.ts` (Telescope/CAWS monitor `rds_innodb_deadlocks_production`):

```promql
min_over_time(avg by (dbinstanceidentifier, db_role) (mysql_innodb_metrics_lock_deadlocks:avg_without_instance{db_cluster="biztech", db_env=~"production|internal", db_role=~"master|replica"})[15m:])
```

- Recording rule: `matik:rds_biztech_innodb_deadlocks:min_over_time_15m_avg`
- Metric: `mysql_innodb_metrics_lock_deadlocks:avg_without_instance` (`rds-monitoring` tenant). The `min_over_time(...[15m])` requires the deadlock signal to stay non-zero for the whole window — so the alert fires on a sustained floor, not a single transient deadlock.
- Threshold / window: `> 0`, `for: 1m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Concurrent writes to the same rows** — e.g. a Historian backfill and the real-time Chronicler path upserting the same entities, or parallel Scribe upserts. Confirm via the involved tables and job activity.
- **Another biztech service** — the cluster is shared; the contending transactions may not be Matik's. Confirm via InnoDB status.
- **Bad recent deployment** — a Matik change altered transaction scope, lock order, or introduced a hot-row update. Confirm via rollout history.
- **Schema/migration activity** — a long-running DDL or migration holding locks. Confirm with the DB owner.

### Related docs

- [DB Management](../operations/db-management.md)
- [VPC Endpoint for RDS](../architecture/vpc-endpoint-rds.md)
- Related runbooks: [RDS CPU Saturation](infra-rds-cpu-saturation.md), [RDS Storage Exhausted](infra-rds-storage-exhausted.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
