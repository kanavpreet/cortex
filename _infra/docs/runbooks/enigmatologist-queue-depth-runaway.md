# Matik Enigmatologist Queue Depth Runaway

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `enigmatologist_queue_depth_runaway_production`
- Symptoms: The Enigmatologist SQS queue depth has exceeded 5,000 messages; the correlation worker is critically behind.

## Impact

The Enigmatologist consumes correlation requests from its dedicated SQS queue. When depth runs away, the worker cannot keep up with the ingest rate: **correlation latency grows** and results may arrive out-of-order in the Scribe (per [enigmatologist architecture](../architecture/enigmatologist/enigmatologist.md)). This is a throughput problem — distinct from the worker being dead ([Worker Liveness](enigmatologist-worker-liveness.md)), though individual messages failing and retrying can also cause depth to climb. Throughput is bounded by `max_concurrent_correlations` and per-message latency (dominated by Matik API reads and the LLM call). See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to inspect the Enigmatologist SQS queue
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/afpxtb0zve8zka?orgId=1)

## Steps

1. Confirm the worker is alive and polling (rule out a dead worker driving the backlog).

   ```promql
   sum(rate(matik_sqs_poll_total{service="enigmatologist", deployment_environment="production"}[5m]))
   ```

2. Confirm whether processing failures are inflating the backlog via retries.

   ```promql
   sum(rate(matik_sqs_failed_messages_total{service="enigmatologist", deployment_environment="production"}[5m]))
   ```

3. Check per-message processing latency (slow correlations reduce throughput).

   ```promql
   histogram_quantile(0.95, sum by (le) (rate(matik_sqs_processing_seconds_bucket{service="enigmatologist", deployment_environment="production"}[5m])))
   ```

4. Check the worker pod for CPU/memory pressure.

   ```bash
   kubectl -n matik-production top pods -l app=matik-enigmatologist-production
   ```

5. Check whether the Matik API or LLM dependency is slow (the dominant per-message cost).

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --tail=200 | grep -iE "timeout|slow|ratelimit|facade|bedrock"
   ```

6. If the worker is dead or not polling, follow [Worker Liveness](enigmatologist-worker-liveness.md).

7. List recent deployments to check whether throughput dropped after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-enigmatologist-production
   ```

8. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

9. If the worker is healthy but simply behind a traffic surge, scale out the deployment to add consumers.

   ```bash
   kubectl -n matik-production scale deploy/matik-enigmatologist-production --replicas=3
   ```

   TODO: confirm the canonical scaling mechanism (replicas vs. `max_concurrent_correlations` tunable in `matik-enigmatologist-config.yml` vs. HPA) and safe values for production.

## Verify

Confirm the queue depth is draining back below threshold.

```promql
max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enig-.*production.*", queue_name!~".*dlq.*"})
```

Expected output: a steadily falling depth back under `5000` and trending toward zero; the alert clears in #matik-alerts.

Confirm processed throughput exceeds the inflow.

```promql
sum(rate(matik_sqs_processed_messages_total{service="enigmatologist", deployment_environment="production"}[5m]))
```

Expected output: a processed rate high enough to drain the backlog.

## Escalate

| Condition | Action |
| --- | --- |
| Depth still climbing after 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| `matik-api` slow/unhealthy | See [API runbooks](api-p95-latency-high.md); coordinate with Ops Eng |
| `llm-fusion-hub` slow / rate-limited | Page the LLM Fusion Hub owning team |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If throughput dropped after a release, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-enigmatologist-production
```

TODO: add the Matik Enigmatologist Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `enigmatologist/enigmatologist_queue_depth_runaway.ts` (Telescope/CAWS monitor `enigmatologist_queue_depth_runaway_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-enig-.*production.*", queue_name!~".*dlq.*"})
```

- Recording rule: `matik:enigmatologist_queue_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; the `dlq` queues are excluded). Not a Matik OTel metric.
- Threshold / window: `> 5000` messages, `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Throughput below inflow** — a traffic surge or reduced capacity. Throughput is bounded by `max_concurrent_correlations` and per-message latency. Confirm via processing-latency p95 (step 3) and inflow vs. processed rate.
- **Slow dependencies** — slow Matik API reads/writes or slow/throttled LLM calls inflate per-message time and starve throughput. Confirm via logs (step 5).
- **Processing failures driving retries** — failed messages return to the queue and re-inflate depth. Confirm via the failure rate (step 2).
- **Worker dead / not polling** — no consumer draining the queue. Confirm via poll rate (step 1) → [Worker Liveness](enigmatologist-worker-liveness.md).
- **Bad recent deployment** — a regression reduced throughput. Confirm by correlating with the latest rollout.

### Related docs

- [Enigmatologist Architecture](../architecture/enigmatologist/enigmatologist.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Worker Liveness](enigmatologist-worker-liveness.md), [DLQ Non-Empty](enigmatologist-dlq-non-empty.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
