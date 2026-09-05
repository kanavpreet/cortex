# Biztech RDS CPU Saturation

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `rds_cpu_saturation_production`
- Symptoms: The shared biztech RDS cluster has sustained CPU above 95% for 10 minutes; query latency spikes are imminent.

## Impact

This alerts on the **shared biztech RDS cluster** — Matik does **not** own this database; many biztech services share it. Sustained CPU saturation causes query timeouts and cascading failures across **all** biztech services, Matik included. For Matik specifically, slow queries surface as `matik-api` latency/5xx (the API's only dependency is MySQL via `biztech-master.proxysql-production`), which then degrades every Matik service that calls the API. Because the cluster is shared, **Matik is often a victim, not the cause**, and the primary action is to identify the top consumer and escalate to the DB infra owner. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)
- [ ] DB access / contact for the shared biztech RDS cluster — see [db-management](../operations/db-management.md)
- [ ] Escalation contact for the biztech RDS / database infra owner

## Steps

1. Confirm which RDS instance/role is saturated.

   ```promql
   avg by (db_instance_identifier, db_role) (aws_rds_cpu_utilization{db_cluster="biztech", db_env="production", db_instance_identifier!="", quantile="1"})
   ```

2. Determine whether the saturated instance is the `master` (writes) or a `replica` (reads) via the `db_role` label — this scopes the impact.

3. Check whether Matik is a significant contributor: review Matik API DB activity and recent query volume.

   ```bash
   kubectl -n matik-production logs -l app=matik-api-production --tail=200 | grep -iE "slow|timeout|query|deadlock"
   ```

4. Check whether a Matik-side surge is driving load (e.g. a large Historian backfill hammering writes via the API).

   ```bash
   kubectl -n matik-production get jobs -l 'app in (matik-historian-jira-production,matik-historian-incidentio-production,matik-historian-biztech-github-production)' --sort-by=.metadata.creationTimestamp | tail
   ```

5. Identify the top query consumers via the cluster's slow query logs / performance insights.

   TODO: add the canonical way to access biztech RDS slow query logs / Performance Insights (console link or `#` channel).

6. If a Matik workload is a top consumer (e.g. a runaway backfill), throttle or pause it — suspend the offending historian CronJob.

   ```bash
   kubectl -n matik-production patch cronjob <matik-historian-job> -p '{"spec":{"suspend":true}}'
   ```

   Remember to un-suspend it after the incident.

7. If the top consumer is **not** Matik, or the cluster needs scaling, escalate to the biztech RDS owner immediately (see [Escalate](#escalate)) — Matik cannot scale a database it does not own.

## Verify

Confirm cluster CPU has dropped below threshold.

```promql
avg by (db_instance_identifier, db_role) (aws_rds_cpu_utilization{db_cluster="biztech", db_env="production", db_instance_identifier!="", quantile="1"})
```

Expected output: value below `95` (percent) sustained for at least 10 minutes; the alert clears in #matik-alerts.

Confirm Matik API health has recovered.

```bash
kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
```

Expected output: `{"status": "ready", ... "checks": {"database": "ok"}}`.

## Escalate

| Condition | Action |
| --- | --- |
| Top consumer is not Matik, or cluster needs scaling | Escalate immediately to the biztech RDS / database infra owner (TODO: confirm team/channel) — Matik does not own this cluster |
| Matik API degraded as a result | See [API runbooks](api-availability-low.md) |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) and the DB infra on-call |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.
- Biztech RDS / DB infra owner: TODO — confirm team and escalation channel.

## Rollback

There is no Matik deploy to roll back for a shared-DB CPU event. If a Matik release introduced an inefficient query driving load, roll back the offending service.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_rds_cpu_saturation.ts` (Telescope/CAWS monitor `rds_cpu_saturation_production`):

```promql
avg by (db_instance_identifier, db_role) (aws_rds_cpu_utilization{db_cluster="biztech", db_env="production", db_instance_identifier!="", quantile="1"})
```

- Recording rule: `matik:rds_biztech_cpu_utilization:avg`
- Metric: `aws_rds_cpu_utilization` (CloudWatch, `cloudwatch` tenant). Per-instance (not a global average) so a hot primary is not averaged away; `quantile="1"` dedupes the two CloudWatch quantile series.
- Threshold / window: `> 95` (percent), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Another biztech service** — the cluster is shared; a non-Matik workload may be the top consumer. Confirm via slow query logs / Performance Insights.
- **Matik write surge** — a large Historian backfill driving heavy writes through the API. Confirm via job activity (step 4); throttle the historian.
- **Inefficient query / missing index** — a Matik (or other) query regression. Confirm via slow query logs and recent deploys.
- **Undersized cluster** — steady-state load exceeds capacity. Owned by the DB infra team to scale.

### Related docs

- [DB Management](../operations/db-management.md)
- [VPC Endpoint for RDS](../architecture/vpc-endpoint-rds.md)
- Related runbooks: [RDS Storage Exhausted](infra-rds-storage-exhausted.md), [RDS InnoDB Deadlocks](infra-rds-deadlocks.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
