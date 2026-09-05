# Matik Chronicler Ingest Stopped

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `chronicler_ingest_stopped_production`
- Symptoms: The Chronicler's Kafka poll loop has stopped ticking for 10 minutes; the entire enrichment and correlation pipeline is idle.

## Impact

Chronicler is the real-time ingestion service — a long-running Kafka consumer that subscribes to Yoyo callback topics for GitHub, JIRA, Incident.io, and the generic Matik webhook (e.g. OpsBot's incident-channel-summary feed) and fans events out to the Scribe and Enricher SQS queues (per [chronicler architecture](../architecture/chronicler.md)). When ingest stops, **no new events enter the Matik pipeline**: nothing reaches Scribe (persistence) or Enricher (LLM enrichment), so the catalog goes stale and correlation halts. This alert now monitors **consumer liveness** (the Kafka poll loop), not message volume: the poll loop ticks at a steady ~1 poll/sec (bounded by the 1.0s `poll_timeout_seconds`, see [chronicler observability](../architecture/chronicler.md#observability)) whether or not any messages are waiting, so a genuine lull in upstream webhook traffic does **not** trigger this alert — only an actual stalled/crashed/deadlocked consumer or lost Kafka connection does. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Chronicler Dashboard](https://grafana.a.musta.ch/goto/efpxs206qwdtsb?orgId=1)
- [ ] `#data-infra-support` contact for Kafka broker / topic / ACL issues

## Steps

1. Confirm the consumer pod is running and not crash-looping.

   ```bash
   kubectl -n matik-production get pods -l app=matik-chronicler-production
   ```

2. Inspect the consumer logs for the subscription line and any errors.

   ```bash
   kubectl -n matik-production logs -l app=matik-chronicler-production --tail=200
   ```

3. Confirm the Kafka poll rate is actually at/near zero (the primary signal for this alert).

   ```promql
   sum(rate(matik_chronicler_kafka_poll_duration_seconds_count{deployment_environment="production"}[10m]))
   ```

4. Check whether the receive rate is also zero, to gauge whether this is a genuine outage or a quiet traffic period with a stalled consumer.

   ```promql
   sum(rate(matik_chronicler_messages_received_total{deployment_environment="production"}[5m]))
   ```

5. Check whether Kafka poll errors are occurring (points at a broker/connectivity problem).

   ```promql
   sum(rate(matik_chronicler_kafka_errors_total{deployment_environment="production"}[5m]))
   ```

6. List recent deployments to check whether ingest stopped after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-chronicler-production
   ```

7. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

8. If the pod is healthy but consuming nothing and no Kafka errors appear, restart the deployment to force a fresh subscription and group rejoin.

   ```bash
   kubectl -n matik-production rollout restart deploy/matik-chronicler-production
   ```

9. If Kafka errors are present (broker unreachable, ACL revoked, topic missing), escalate to `#data-infra-support` (see [Escalate](#escalate)).

## Verify

Confirm the Kafka poll rate has recovered above the alert floor.

```promql
sum(rate(matik_chronicler_kafka_poll_duration_seconds_count{deployment_environment="production"}[10m]))
```

Expected output: a value above `0.5` polls/sec (healthy baseline is ~1/sec) sustained for at least 10 minutes; the alert clears in #matik-alerts.

Confirm events are flowing again (secondary signal — may legitimately stay near zero during a quiet period even though ingest is healthy).

```promql
sum(rate(matik_chronicler_messages_received_total{deployment_environment="production"}[5m]))
```

Expected output: a non-zero receive rate once real traffic arrives.

Confirm the logs show callbacks being processed.

```bash
kubectl -n matik-production logs -l app=matik-chronicler-production --tail=50 | grep -i "Callback processed"
```

Expected output: recent `Callback processed` log lines.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Kafka broker unreachable, topic missing, or consumer-group ACL revoked | Contact `#data-infra-support` (owns the Kafka bus and `yoyo.callback.matik_*` topics) |
| Upstream provider (Yoyo / GHE / JIRA / Incident.io) not delivering | Confirm with the provider integration owner |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie), #data-infra-support (Kafka)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If ingest stopped after a release, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-chronicler-production
```

TODO: add the Matik Chronicler Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `chronicler/chronicler_ingest_stopped.ts` (Telescope/CAWS monitor `chronicler_ingest_stopped_production`):

```promql
sum(rate(matik_chronicler_kafka_poll_duration_seconds_count{deployment_environment="production"}[10m]))
```

- Recording rule: `matik:chronicler_kafka_poll_rate:sum`
- Metric: `matik_chronicler_kafka_poll_duration_seconds` (Histogram; poll-loop latency — the alert uses `_count` for the poll rate, see [chronicler observability](../architecture/chronicler.md#observability)). Recorded each loop iteration in `matik/chronicler/consumer.py`.
- Threshold / window: `< 0.5` polls/s, `for: 10m`. The consumer loop blocks for up to `chronicler.kafka.poll_timeout_seconds` (`1.0`s in production, set under `common.all` with no prod override — see `_infra/kube/kube-gen.yml`) per iteration and increments the poll counter once per iteration regardless of whether a message arrived, so the idle baseline is ~1 poll/sec, giving ~2x headroom over this floor. TODO: confirm the live baseline against the Grafana dashboard and re-verify this headroom.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.
- This replaces a prior alert on `rate(matik_chronicler_messages_received_total[5m]) < 0.001` (message volume). That alert paged during legitimate quiet periods — Chronicler's input is bursty business traffic that has been observed to drop to zero for ~17h continuous stretches (overnight/weekends) while ingestion was perfectly healthy. Monitoring the poll loop instead catches only genuine consumer failures (stall, crash, deadlock, lost Kafka connection) and is immune to traffic patterns.

### Likely causes (most → least likely)

- **Kafka connectivity lost** — broker unreachable, consumer-group ACL revoked, or topic renamed. Chronicler's only dependency is `kafka-prod-a.kafka-prod-a` (`_infra/mesh.yml`). Confirm via the Kafka error rate (step 5) and logs.
- **Consumer wedged / not polling** — the consumer loop stalled or the pod is unhealthy. Confirm via pod status and absence of recent log activity; mitigate with a restart.
- **Bad recent deployment** — a regression broke subscription, config loading, or the poll loop itself. Confirm by correlating with the latest rollout.
- **No upstream traffic** — a genuine lull, or Yoyo / a provider stopped delivering webhooks. This no longer trips this alert on its own (the poll loop keeps ticking), but confirm via step 4 if you want to rule out a coincidental traffic lull while triaging.

### Related docs

- [Chronicler Architecture](../architecture/chronicler.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Kafka Errors](chronicler-kafka-errors.md), [Publish Failures](chronicler-publish-failures.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
