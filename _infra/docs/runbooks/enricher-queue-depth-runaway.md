# Matik Enricher Queue Depth Runaway

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `enricher_queue_depth_runaway_production`
- Symptoms: The Enricher SQS queue depth has exceeded 5,000 messages; the worker is critically behind.

## Impact

The Enricher consumes enrichment requests produced by the Historian (batch crawls) and Chronicler (real-time webhooks). When depth runs away, the worker cannot keep up with the ingest rate: **events accumulate and enrichment latency grows unbounded** — LLM summaries lag behind the base records already written by Scribe (per [enricher design](../architecture/enricher-design.md)). This is a throughput problem — sustained per-message retries/backoffs reduce effective throughput and can drive depth up. Throughput is bounded by `max_concurrent_llm_calls × replicas` and per-message Facade latency. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to inspect the Enricher SQS queue
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/dfpxtsorldou8f?orgId=1)

## Steps

1. Confirm the worker is processing (rule out a stalled consumer driving the backlog).

   ```promql
   sum(rate(matik_enricher_messages_processed_total{deployment_environment="production"}[5m]))
   ```

2. Confirm whether processing failures/backoffs are throttling throughput.

   ```promql
   sum(rate(matik_enricher_backoff_total{deployment_environment="production"}[5m]))
   ```

3. Check per-message processing latency (slow Facade calls reduce throughput).

   ```promql
   histogram_quantile(0.95, sum by (le) (rate(matik_enricher_message_processing_duration_seconds_bucket{deployment_environment="production"}[5m])))
   ```

4. Check the worker pod for CPU/memory pressure and current replica count.

   ```bash
   kubectl -n matik-production top pods -l app=matik-enricher-production
   kubectl -n matik-production get deploy/matik-enricher-production
   ```

5. Check whether Facade (LLM) is slow or rate-limiting (the dominant per-message cost).

   ```bash
   kubectl -n matik-production logs -l app=matik-enricher-production --tail=200 | grep -iE "facade|llm|timeout|ratelimit|429"
   ```

6. If individual messages are failing/backing off, check the backoff rate and DLQ (see [DLQ Non-Empty](enricher-dlq-non-empty.md)).

7. List recent deployments to check whether throughput dropped after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-enricher-production
   ```

8. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

9. If the worker is healthy but behind a traffic surge (e.g. a large Historian backfill), scale out to add consumers. Each pod polls independently and total Facade concurrency scales linearly.

   ```bash
   kubectl -n matik-production scale deploy/matik-enricher-production --replicas=3
   ```

   TODO: confirm the canonical scaling mechanism (replicas vs. `max_concurrent_llm_calls` in `matik-enricher-config.yml` vs. HPA) and safe values — total Facade concurrency is `max_concurrent_llm_calls × replicas` and must respect Facade rate limits.

## Verify

Confirm the queue depth is draining back below threshold.

```promql
max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enr-.*production.*", queue_name!~".*dlq.*"})
```

Expected output: a steadily falling depth back under `5000` and trending down; the alert clears in #matik-alerts.

Confirm processed throughput exceeds inflow.

```promql
sum(rate(matik_enricher_messages_processed_total{status="success", deployment_environment="production"}[5m]))
```

Expected output: a processed rate high enough to drain the backlog.

## Escalate

| Condition | Action |
| --- | --- |
| Depth still climbing after 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| `llm-fusion-hub` slow / rate-limited | Page the LLM Fusion Hub owning team |
| `matik-api` slow (hash fetch) | See [API runbooks](api-p95-latency-high.md); coordinate with Ops Eng |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If throughput dropped after a release, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-enricher-production
```

TODO: add the Matik Enricher Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `enricher/enricher_queue_depth_runaway.ts` (Telescope/CAWS monitor `enricher_queue_depth_runaway_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-enr-.*production.*", queue_name!~".*dlq.*"})
```

- Recording rule: `matik:enricher_queue_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; the `dlq` queues are excluded). Not a Matik OTel metric — works even when the Enricher is down.
- Threshold / window: `> 5000` messages, `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Throughput below inflow** — a large Historian backfill or webhook surge exceeds capacity. Throughput is bounded by `max_concurrent_llm_calls × replicas` and per-message Facade latency. Confirm via processing-latency p95 (step 3) and inflow vs. processed rate.
- **Slow / rate-limited Facade** — slow or throttled LLM calls inflate per-message time. Confirm via logs (step 5) and the backoff rate.
- **Processing failures driving backoffs** — retryable failures repeatedly defer messages, reducing effective drain. Confirm via the backoff rate (step 2); sustained failures can also feed the DLQ, see [DLQ Non-Empty](enricher-dlq-non-empty.md).
- **Reduced capacity** — fewer healthy replicas or resource pressure. Confirm via pod status and `kubectl top`.
- **Bad recent deployment** — a regression reduced throughput. Confirm by correlating with the latest rollout.

Note the hash-dedup + in-memory cache normally suppress redundant Facade calls (unchanged content is skipped), so a depth runaway often coincides with genuinely new/changed content or a dependency slowdown.

### Related docs

- [Enricher Design](../architecture/enricher-design.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [DLQ Non-Empty](enricher-dlq-non-empty.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
