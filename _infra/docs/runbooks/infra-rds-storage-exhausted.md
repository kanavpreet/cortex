# Biztech RDS Storage Exhausted

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `rds_storage_exhausted_production`
- Symptoms: The shared biztech RDS cluster storage utilization has exceeded 95%; write failures and data-corruption risk are imminent.

## Impact

This alerts on the **shared biztech RDS cluster** — Matik does **not** own this database. Storage above 95% risks **write failures and data corruption for all biztech services**, Matik included. For Matik, write failures surface as `matik-api` 5xx (the API writes through MySQL), which stalls Scribe persistence and every write path (historians, chronicler, enricher, enigmatologist results). This is the most urgent of the RDS alerts: storage exhaustion is a hard stop, and the fix (expand storage / purge data) is owned by the DB infra team. **Escalate immediately** rather than triaging at length. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)
- [ ] DB access / contact for the shared biztech RDS cluster — see [db-management](../operations/db-management.md)
- [ ] Escalation contact for the biztech RDS / database infra owner (this alert requires their action)

## Steps

1. Confirm the current storage utilization.

   ```promql
   avg(aws_rds_volume_bytes_used:average{db_cluster="biztech-us-east-2"}) / 1.40737488355328e14 * 100
   ```

2. Escalate to the biztech RDS / database infra owner **immediately** — only they can expand storage or safely purge cluster data (see [Escalate](#escalate)). Do this in parallel with the remaining steps.

3. Confirm whether Matik writes are failing as a result.

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
   kubectl -n matik-production logs -l app=matik-api-production --tail=200 | grep -iE "write|disk|full|read-only|errno"
   ```

4. Reduce Matik's write pressure to slow further growth — suspend the historian CronJobs (the largest bulk writers) until storage is recovered.

   ```bash
   kubectl -n matik-production patch cronjob matik-historian-incidentio-production -p '{"spec":{"suspend":true}}'
   kubectl -n matik-production patch cronjob matik-historian-jira-production -p '{"spec":{"suspend":true}}'
   kubectl -n matik-production patch cronjob matik-historian-biztech-github-production -p '{"spec":{"suspend":true}}'
   ```

   Remember to un-suspend them once storage is recovered.

5. Help identify whether a Matik table is contributing disproportionate growth (for the DB owner's triage).

   TODO: add the query/dashboard for per-table/per-schema size on the biztech cluster, and confirm whether Matik owns any unbounded-growth tables.

6. Once the DB owner has expanded storage / purged data and utilization drops, un-suspend the historian CronJobs (reverse step 4).

## Verify

Confirm storage utilization has dropped below threshold.

```promql
avg(aws_rds_volume_bytes_used:average{db_cluster="biztech-us-east-2"}) / 1.40737488355328e14 * 100
```

Expected output: value below `95` (percent) with headroom; the alert clears in #matik-alerts.

Confirm Matik writes succeed again.

```bash
kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
```

Expected output: `{"status": "ready", ... "checks": {"database": "ok"}}`.

## Escalate

| Condition | Action |
| --- | --- |
| Always — on alert fire | Escalate immediately to the biztech RDS / database infra owner (TODO: confirm team/channel); only they can expand storage or purge cluster data |
| Matik API writes failing | See [API runbooks](api-availability-low.md) |
| Not acknowledged by DB infra promptly | Page Matik on-call via PagerDuty (service name TODO) and the DB infra on-call |
| Matik table identified as the growth driver | Post in #matik-internal to plan retention/cleanup |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.
- Biztech RDS / DB infra owner: TODO — confirm team and escalation channel.

## Rollback

Not applicable — storage exhaustion is not a Matik deploy event. If a Matik release introduced runaway data growth, roll back the offending service and plan a data cleanup.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_rds_storage_exhausted.ts` (Telescope/CAWS monitor `rds_storage_exhausted_production`):

```promql
avg(aws_rds_volume_bytes_used:average{db_cluster="biztech-us-east-2"}) / 1.40737488355328e14 * 100
```

- Recording rule: `matik:rds_biztech_storage_pct:avg`
- Metric: `aws_rds_volume_bytes_used:average` (CloudWatch, `cloudwatch` tenant). Percentage = used / 128 TiB (`1.40737488355328e14` bytes, the Aurora max volume size), matching the dashboard.
- Threshold / window: `> 95` (percent), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Organic growth across the shared cluster** — many biztech services accumulating data; not Matik-specific. Owned by DB infra to expand.
- **Unbounded Matik table** — a Matik table without retention/cleanup growing steadily. Confirm via per-table size (step 5).
- **Runaway write loop** — a bug or backfill writing far more than expected. Confirm via write-rate metrics and recent deploys.
- **Large index / temp space** — schema or migration activity consuming space. Confirm with the DB owner.

### Related docs

- [DB Management](../operations/db-management.md)
- [VPC Endpoint for RDS](../architecture/vpc-endpoint-rds.md)
- Related runbooks: [RDS CPU Saturation](infra-rds-cpu-saturation.md), [RDS InnoDB Deadlocks](infra-rds-deadlocks.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
