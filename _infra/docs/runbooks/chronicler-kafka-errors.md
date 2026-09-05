# Matik Chronicler Kafka Errors

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `chronicler_kafka_errors_production`
- Symptoms: The Chronicler's message processing error rate has exceeded 7.2% over a 1-hour window; events are failing signature validation or otherwise failing processing.

## Impact

Chronicler's only dependency is the Kafka bus (`kafka-prod-a.kafka-prod-a`, per `_infra/mesh.yml`); it consumes Yoyo callback topics and fans events to Scribe and Enricher. This alert now measures the **processing error ratio** (failed / total processed messages) rather than a raw Kafka-error count — a sustained high ratio means a meaningful fraction of incoming events are failing (today, exclusively via `signature_failed`), so those events never reach Scribe or Enricher and the catalog/enrichment pipeline degrades for the affected events. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Chronicler Dashboard](https://grafana.a.musta.ch/goto/efpxs206qwdtsb?orgId=1)
- [ ] `#data-infra-support` contact for Kafka broker / topic / ACL issues

## Steps

1. Confirm the consumer pod status.

   ```bash
   kubectl -n matik-production get pods -l app=matik-chronicler-production
   ```

2. Inspect the logs for the signature-validation failure detail and any Kafka-level errors.

   ```bash
   kubectl -n matik-production logs -l app=matik-chronicler-production --tail=200 | grep -iE "signature|kafka"
   ```

3. Confirm the current error ratio and processed volume (the alert requires >= 30 processed messages in the 1h window before it fires).

   ```promql
   sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production", status="signature_failed"}[1h])) / sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production"}[1h]))
   ```

4. Confirm whether ingest has also stalled (a genuine hard outage shows both a high error ratio and a stopped poll loop).

   ```promql
   sum(rate(matik_chronicler_kafka_poll_duration_seconds_count{deployment_environment="production"}[10m]))
   ```

5. List recent deployments to check whether errors started after a release or config change.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-chronicler-production
   ```

6. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

7. If the errors indicate broker unavailability, ACL/auth failure, or a missing topic, escalate to `#data-infra-support` (see [Escalate](#escalate)).

8. If the errors are transient/connection-level and persist, restart the deployment to re-establish the broker connection and rejoin the consumer group.

   ```bash
   kubectl -n matik-production rollout restart deploy/matik-chronicler-production
   ```

## Verify

Confirm the processing error ratio has dropped back below the 7.2% fast-burn threshold.

```promql
sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production", status="signature_failed"}[1h])) / sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production"}[1h]))
```

Expected output: value at or below `0.072` sustained for the 1h window; the alert clears in #matik-alerts.

Confirm ingest has resumed and is processing successfully.

```promql
sum(rate(matik_chronicler_messages_received_total{deployment_environment="production"}[5m]))
```

Expected output: a non-zero, steady receive rate.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Broker unreachable, auth/ACL failure, or topic missing | Contact `#data-infra-support` (owns the Kafka bus and `yoyo.callback.matik_*` topics) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie), #data-infra-support (Kafka)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If errors began after a release, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-chronicler-production
```

TODO: add the Matik Chronicler Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `chronicler/chronicler_kafka_errors.ts` (Telescope/CAWS monitor `chronicler_kafka_errors_production`):

```promql
sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production", status="signature_failed"}[1h])) / sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production"}[1h])) and sum(increase(matik_chronicler_messages_processed_total{deployment_environment="production"}[1h])) >= 30
```

- Recording rule: `matik:chronicler_ingest_error_ratio:rate1h`
- Metric: `matik_chronicler_messages_processed_total` (Counter; dimensioned by `status`, see [chronicler observability](../architecture/chronicler.md#observability)). The alert is the ratio of `status="signature_failed"` messages to all processed messages, guarded by a minimum of 30 processed messages in the window (`hasVolume`) so a single stray error during a quiet stretch cannot produce a misleading 100% ratio. `filtered` is an intentional drop, not an error, and is excluded from the numerator. This is an SLO burn-rate alert: fast-burn threshold = 14.4 x (1 - 0.995) = 7.2%.
- Threshold / window: `> 0.072` (7.2%) error ratio, evaluated over a 1h lookback (chosen because Chronicler throughput is low/bursty — median ~60 msgs/hour — so a 5m window is statistically meaningless), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.
- This replaces a prior alert on `rate(matik_chronicler_kafka_errors_total[5m]) > 0.01`. That metric **does not exist** in the matik tenant, so the old alert could never fire — this rewrite is a genuine bug fix, not just a tuning change.

### Likely causes (most → least likely)

- **Signature validation failures** — the only error status currently emitted is `signature_failed`; a spike usually means a webhook signing-secret mismatch or a malformed payload from a provider. Confirm via the log grep (step 2) and the error-ratio breakdown (step 3).
- **Broker unavailable / connectivity loss** — the Kafka bus (`kafka-prod-a.kafka-prod-a`) is unreachable or flapping, which can also degrade processing. Confirm via logs; owned by `#data-infra-support`.
- **Auth / ACL revoked** — the consumer-group ACL on a `yoyo.callback.matik_*` topic was removed (granted per-env via `#data-infra-support`). Confirm via auth errors in logs.
- **Topic missing or renamed** — a subscribed topic no longer exists. Confirm via topic-not-found errors.
- **Bad recent deployment / config** — a signing-secret rotation, kafka config change, or processing regression. Confirm by correlating with the latest rollout.

### Related docs

- [Chronicler Architecture](../architecture/chronicler.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Ingest Stopped](chronicler-ingest-stopped.md), [Publish Failures](chronicler-publish-failures.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
