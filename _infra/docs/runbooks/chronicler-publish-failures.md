# Matik Chronicler Publish Failures

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `chronicler_publish_failures_production`
- Symptoms: The Chronicler's SQS publish failure ratio has exceeded 7.2% over a 1-hour window; data is not reliably reaching downstream workers.

## Impact

After validating and shaping an event, Chronicler publishes a sanitized base message to the **Scribe HP** queue and an enrichment request to the **Enricher** queue. The Kafka offset is committed only after both publishes succeed — so a publish failure means **no commit, and Kafka redelivers** the record (at-least-once; Scribe is upsert-safe). This alert measures the **ratio** of non-success publishes to all publishes over a 1-hour window: a sustained high ratio means a significant fraction of events are not reaching the Enricher, Enigmatologist, or Scribe on the first attempt, and the downstream pipeline is degraded. While redelivery protects against data loss, a persistent SQS/IAM problem will back ingest up behind the failing records. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to inspect the Scribe HP and Enricher SQS queues
- [ ] Grafana access for the [Matik Chronicler Dashboard](https://grafana.a.musta.ch/goto/efpxs206qwdtsb?orgId=1)

## Steps

1. Confirm the consumer pod status.

   ```bash
   kubectl -n matik-production get pods -l app=matik-chronicler-production
   ```

2. Inspect the logs for publish errors and the SQS exception detail.

   ```bash
   kubectl -n matik-production logs -l app=matik-chronicler-production --tail=200 | grep -iE "publish|sqs|scribe|enricher"
   ```

3. Identify which target is failing (Scribe vs. Enricher) and its status label. The alert matches any `status != "success"`, not a hardcoded `"failed"` value, so check what status is actually being emitted.

   ```promql
   sum by (target, status) (rate(matik_chronicler_publish_outcome_total{deployment_environment="production", status!="success"}[5m]))
   ```

4. Confirm the failing queue exists and is reachable (look up the URL from the config params, then query its attributes).

   ```bash
   aws sqs get-queue-attributes --queue-url "$SCRIBE_HP_QUEUE_URL" --attribute-names All
   ```

   TODO: confirm the concrete Scribe HP and Enricher queue URLs/regions for production (rendered from `Env.Params` into `_infra/kube/files/matik-chronicler-config.yml`).

5. Check for IAM/permission errors in the logs (the Chronicler's role must allow `sqs:SendMessage` on both queues).

   ```bash
   kubectl -n matik-production logs -l app=matik-chronicler-production --tail=200 | grep -iE "accessdenied|not authorized|credential"
   ```

6. List recent deployments to check whether failures started after a release or config change.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-chronicler-production
   ```

7. If a recent deploy or queue/IAM config change is implicated, roll it back (see [Rollback](#rollback)).

8. If the queue or IAM is the problem, escalate to the SQS/IAM owner (see [Escalate](#escalate)).

9. Once the downstream issue is fixed, confirm redelivery drains the backlog (no restart needed — uncommitted records redeliver automatically).

## Verify

Confirm the publish failure ratio has dropped back below the 7.2% fast-burn threshold.

```promql
sum(rate(matik_chronicler_publish_outcome_total{status!="success", deployment_environment="production"}[1h])) / sum(rate(matik_chronicler_publish_outcome_total{deployment_environment="production"}[1h]))
```

Expected output: value at or below `0.072` sustained for the 1h window; the alert clears in #matik-alerts.

Confirm successful publishes are flowing.

```promql
sum by (target) (rate(matik_chronicler_publish_outcome_total{status="success", deployment_environment="production"}[5m]))
```

Expected output: a steady success rate for both `scribe` and `enricher` targets.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| SQS queue unreachable / throttled / deleted | Escalate to the SQS queue / AWS infra owner |
| IAM `AccessDenied` on `sqs:SendMessage` | Escalate to the IAM/role owner to restore the Chronicler's permissions |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If failures began after a release or config change, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-chronicler-production
```

TODO: add the Matik Chronicler Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `chronicler/chronicler_publish_failures.ts` (Telescope/CAWS monitor `chronicler_publish_failures_production`):

```promql
sum(rate(matik_chronicler_publish_outcome_total{status!="success", deployment_environment="production"}[1h])) / sum(rate(matik_chronicler_publish_outcome_total{deployment_environment="production"}[1h])) and sum(increase(matik_chronicler_publish_outcome_total{deployment_environment="production"}[1h])) >= 5
```

- Recording rule: `matik:chronicler_publish_failure_ratio:rate1h`
- Metric: `matik_chronicler_publish_outcome_total` (downstream publish outcomes, dimensioned by `target` = `scribe`/`enricher` and `status`; OTel instrument `chronicler.publish`, see [chronicler observability](../architecture/chronicler.md#observability)). Recorded in `matik/chronicler/consumer.py` on each Scribe/Enricher publish. The alert matches `status != "success"` (not a hardcoded failure value) so any failure label the service emits is caught automatically, guarded by a minimum of 5 publishes in the window so a single failure during a quiet stretch cannot produce a misleading 100% ratio. This is an SLO burn-rate alert: fast-burn threshold = 14.4 x (1 - 0.995) = 7.2%.
- Threshold / window: `> 0.072` (7.2%) failure ratio, evaluated over a 1h lookback (chosen because publish volume is low/bursty — median ~9 publishes/hour — so a 5m window is statistically meaningless), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.
- This replaces a prior alert on `rate(matik_chronicler_publish_outcome_total{status="failed"}[5m]) > 0.001`. That fixed 5m window is too short for this alert's low/bursty volume (median ~9 publishes/hour) — a single failure can produce a misleading spike, and a quiet 5m window can hide a real failure ratio. The new alert uses a 1h lookback with a minimum-publishes guard and matches `status != "success"` (not a hardcoded failure value) so any failure label the service emits is caught automatically.

### Likely causes (most → least likely)

- **SQS queue unavailable / throttled** — the Scribe HP or Enricher queue is unreachable, throttled, or was deleted/renamed. Confirm via the `target`/`status` breakdown and queue attributes.
- **IAM permission failure** — the Chronicler's role lost `sqs:SendMessage` on a queue. Confirm via `AccessDenied` log lines.
- **Misconfigured queue URL/region** — a bad `Env.Params` value rendered into `matik-chronicler-config.yml`. Confirm by correlating with the latest config change.
- **Enricher publisher returned None** — the `EnrichmentPublisher` swallowed an error and returned `None`; the loop raises so the offset is not committed (`matik/chronicler/consumer.py`). Confirm via the `enricher` target failures and logs.

Note: at-least-once delivery means failed publishes do not lose data — the record redelivers on the next poll once the downstream issue is resolved. Scribe absorbs duplicates via upsert.

### Related docs

- [Chronicler Architecture](../architecture/chronicler.md)
- [Scribe Design](../architecture/scribe-design.md)
- [Enricher Design](../architecture/enricher-design.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Ingest Stopped](chronicler-ingest-stopped.md), [Kafka Errors](chronicler-kafka-errors.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
