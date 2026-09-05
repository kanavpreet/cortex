# Matik Scribe DLQ Non-Empty

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `scribe_dlq_non_empty_production`
- Symptoms: A Scribe dead-letter queue has one or more messages; events were not written to the database.

## Impact

Scribe is the **sole DB writer**. A message reaches a Scribe DLQ in two cases: a **non-retryable error** (malformed JSON, Pydantic validation failure, unknown `source_type`/`message_type`, missing `data`) immediately, or a **retryable error** (DB connection/transient) after SQS `maxReceiveCount` is exhausted (per [scribe design](../architecture/scribe-design.md#error-handling)). Because Scribe is the **tail of the pipeline**, a DLQ message means that event was **never persisted** — the data is effectively lost until the message is remediated and replayed. Each priority tier (`high`/`medium`/`low`) has its own DLQ; the alert regex matches all of them. This is the durable-failure counterpart to the transient DB write errors that precede DLQ growth. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to read, redrive, and delete messages on the Scribe DLQs and main queues
- [ ] DB access — see [db-management](../operations/db-management.md)
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/bfpyv37tq5ptsd?orgId=1)

## Steps

1. Identify which tier's DLQ is non-empty and its depth. The `<tier>` (`high`/`medium`/`low`) extracted here is used to fill in `<tier>` in the commands throughout the rest of this runbook.

   ```promql
   max by (queue_name) (aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-scrb-.*production.*dlq.*"})
   ```

2. Identify the dominant DLQ error type (Scribe labels DLQ routing by `error_type`).

   ```promql
   sum by (source_type, error_type) (rate(matik_scribe_dlq_messages_total{deployment_environment="production"}[1h]))
   ```

3. Look up the DLQ and main queue URLs for the affected tier.

   ```bash
   aws sqs list-queues --queue-name-prefix matik-scrb
   ```

   TODO: confirm the concrete production main-queue and DLQ URLs/regions per tier (rendered into `matik-scribe-<tier>-config.yml`).

4. Read a sample DLQ message without deleting it to identify the failure.

   ```bash
   aws sqs receive-message --queue-url "$SCRIBE_DLQ_URL" --max-number-of-messages 10 --visibility-timeout 0 --attribute-names All --message-attribute-names All
   ```

5. Correlate with the Scribe logs around when the messages were dead-lettered (`<tier>` from step 1).

   ```bash
   kubectl -n matik-production logs -l app=matik-scribe-<tier>-production --since=6h | grep -iE "dlq|error|validation|malformed|deadlock"
   ```

6. Classify the cause and remediate:

   - **Malformed / validation / unknown route** — a producer (Chronicler/Historian/Enricher/Enigmatologist) emitted a bad message; fix the producer. Replaying as-is will fail again.
   - **DB error (max retries exceeded)** — the database was down/overloaded and retries ran out; once the DB recovers these are safe to replay. Check the [RDS runbooks](infra-rds-cpu-saturation.md).

7. Confirm writes are succeeding again before replay (an active fault will refill the DLQ).

   ```promql
   sum(rate(matik_scribe_messages_processed_total{status="success", deployment_environment="production"}[5m]))
   ```

8. Once the cause is fixed, redrive the eligible DLQ messages back onto the tier's main queue. Scribe upserts are idempotent, so replaying an already-written message is safe.

   TODO: confirm the canonical redrive mechanism (AWS SQS console "Start DLQ redrive", an `sqspurger`/redrive script, or a manual `send-message` + `delete-message` loop). Do not delete DLQ messages until confirmed reprocessed. Do not blindly redrive malformed/validation messages — they will fail again.

9. Confirm the redriven messages now persist successfully (no new DLQ arrivals).

## Verify

Confirm the affected DLQ has drained to empty.

```promql
max by (queue_name) (aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-scrb-.*production.*dlq.*"})
```

Expected output: depth `0`; the alert clears in #matik-alerts.

Confirm replayed messages wrote successfully.

```promql
sum by (source_type) (rate(matik_scribe_messages_processed_total{status="success", deployment_environment="production"}[5m]))
```

Expected output: a success-rate bump matching the redriven volume, with no corresponding error-rate rise.

## Escalate

| Condition | Action |
| --- | --- |
| Cannot identify the failure cause from DLQ messages + logs | Post in #matik-internal with a sample message |
| DLQ keeps refilling after remediation | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Caused by DB outage | Follow the [RDS runbooks](infra-rds-cpu-saturation.md) |
| Malformed messages traced to a producer | Coordinate with the producer owner (Chronicler / Historian / Enricher / Enigmatologist) |
| Redrive mechanism unavailable / unclear | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

DLQ remediation does not deploy code, so there is no deploy to roll back. If the underlying failure was a release (schema/DAO regression that fails validation), roll that back before redriving (`<tier>` from step 1).

```bash
kubectl -n matik-production rollout undo deploy/matik-scribe-<tier>-production
```

TODO: add the Matik Scribe Spinnaker pipeline links for all three tiers.

## Appendix

### Alert definition

Defined in `scribe/scribe_dlq_non_empty.ts` (Telescope/CAWS monitor `scribe_dlq_non_empty_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-scrb-.*production.*dlq.*"})
```

- Recording rule: `matik:scribe_dlq_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; matches only the `dlq` queues across all three tiers). Scribe also emits `matik_scribe_dlq_messages_total` (by `error_type`) for cause classification.
- Threshold / window: `>= 1` message, `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Malformed / validation / unknown route (non-retryable)** — a producer emitted a message that fails JSON parsing, Pydantic validation, or `VALID_ROUTES` routing; dead-lettered immediately. Confirm via `error_type` (step 2); fix the producer.
- **DB outage exhausted retries (was retryable)** — repeated DB connection/transient errors burned through `maxReceiveCount`. Confirm via logs around the dead-letter time; safe to redrive once the DB recovers.
- **Schema mismatch after a deploy** — a model/DAO change made a class of messages fail validation. Confirm via rollout history.
- **Enrichment-before-base unresolved** — an enrichment message whose base record never appeared, beyond the `enrichment_base_not_found_delay` retries. Confirm via the message and the corresponding base write.

Because Scribe upserts are idempotent, redriving DB-error messages is safe; malformed/validation messages must be fixed at the producer, not blindly redriven.

### Related docs

- [Scribe Design — Error Handling](../architecture/scribe-design.md#error-handling)
- [DB Management](../operations/db-management.md) · [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Queue Depth Runaway](scribe-queue-depth-runaway.md), [RDS CPU Saturation](infra-rds-cpu-saturation.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
