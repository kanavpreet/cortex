# Matik Enigmatologist DLQ Non-Empty

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `enigmatologist_dlq_non_empty_production`
- Symptoms: The Enigmatologist dead-letter queue has one or more messages; correlation has permanently failed for those events.

## Impact

Each Enigmatologist queue has a paired DLQ. A message lands there after exceeding the maximum receive count — i.e. it failed correlation repeatedly through every retry (per [enigmatologist architecture](../architecture/enigmatologist/enigmatologist.md)). DLQ messages represent events that **will not be correlated without manual intervention**: the correlation data for those incidents/changes is missing until they are remediated and replayed. This alert is the durable-failure counterpart to the transient failures that precede DLQ growth. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to read, replay, and delete messages on the Enigmatologist DLQ and main queue
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/afpxtb0zve8zka?orgId=1)

## Steps

1. Confirm the current DLQ depth.

   ```promql
   max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enig-.*production.*dlq.*"})
   ```

2. Look up the DLQ and main queue URLs.

   ```bash
   aws sqs list-queues --queue-name-prefix matik-enig
   ```

   TODO: confirm the concrete production main-queue and DLQ URLs/regions (rendered from `Env.Params` into `matik-enigmatologist-config.yml`).

3. Read a sample DLQ message without deleting it to identify the failure.

   ```bash
   aws sqs receive-message --queue-url "$ENIG_DLQ_URL" --max-number-of-messages 10 --visibility-timeout 0 --attribute-names All --message-attribute-names All
   ```

4. Correlate the failure cause with the worker logs around when these messages were dead-lettered.

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --since=6h | grep -iE "correlation failed|error|traceback"
   ```

5. Confirm whether new processing failures are still occurring (an active fault will refill the DLQ after replay).

   ```promql
   sum(rate(matik_sqs_failed_messages_total{service="enigmatologist", deployment_environment="production"}[5m]))
   ```

6. Remediate the root cause first (dependency failure, poison-pill payload, or bad deploy — see [Likely causes](#likely-causes-most--least-likely)) before replaying.

7. Once the cause is fixed, replay the DLQ messages back onto the main queue for reprocessing.

   TODO: confirm the canonical replay mechanism (AWS SQS DLQ redrive via the console "Start DLQ redrive", an `sqspurger`/redrive script, or a manual `send-message` + `delete-message` loop). Do not delete DLQ messages until they are confirmed reprocessed.

8. Confirm the reprocessed messages now succeed (no new DLQ arrivals).

## Verify

Confirm the DLQ has drained to empty.

```promql
max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enig-.*production.*dlq.*"})
```

Expected output: depth `0`; the alert clears in #matik-alerts.

Confirm replayed messages processed successfully.

```promql
sum(rate(matik_sqs_processed_messages_total{service="enigmatologist", deployment_environment="production"}[5m]))
```

Expected output: a processed-rate bump matching the replayed volume, with no corresponding failure-rate rise.

## Escalate

| Condition | Action |
| --- | --- |
| Cannot identify the failure cause from DLQ messages + logs | Post in #matik-internal with a sample message |
| DLQ keeps refilling after remediation | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Failure caused by `matik-api` or `llm-fusion-hub` | Page the owning team for that dependency |
| Replay mechanism unavailable / unclear | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

DLQ remediation does not deploy code, so there is no deploy to roll back. If the underlying failure was introduced by a release, roll that back before replaying.

```bash
kubectl -n matik-production rollout undo deploy/matik-enigmatologist-production
```

TODO: add the Matik Enigmatologist Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `enigmatologist/enigmatologist_dlq_non_empty.ts` (Telescope/CAWS monitor `enigmatologist_dlq_non_empty_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-enig-.*production.*dlq.*"})
```

- Recording rule: `matik:enigmatologist_dlq_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; matches only the `dlq` queues). Not a Matik OTel metric.
- Threshold / window: `>= 1` message, `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Persistent dependency failure** — repeated `matik-api` or `llm-fusion-hub` errors exhausted a message's retries. Confirm via the worker logs around the dead-letter time.
- **Poison-pill payload** — a specific message that always fails correlation (unexpected shape that passes JSON parsing but breaks the engine). Confirm by reading the DLQ message.
- **Bug introduced by a deploy** — a regression that fails a class of messages until reverted. Confirm by correlating dead-letter timing with rollout history.

Messages only reach the DLQ after exhausting all main-queue retries, so a non-empty DLQ implies a sustained (not transient) failure for those events.

### Related docs

- [Enigmatologist Architecture](../architecture/enigmatologist/enigmatologist.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Worker Liveness](enigmatologist-worker-liveness.md), [Queue Depth Runaway](enigmatologist-queue-depth-runaway.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
