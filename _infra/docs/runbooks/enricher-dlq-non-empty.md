# Matik Enricher DLQ Non-Empty

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `enricher_dlq_non_empty_production`
- Symptoms: The Enricher dead-letter queue has one or more messages; enrichment has permanently failed for those events.

## Impact

The Enricher routes a message to the DLQ in two cases: a **non-retryable error** (malformed message, Facade 400 bad request, or content-filter) immediately, or a **retryable error** after SQS `maxReceiveCount` is exhausted (per [enricher design](../architecture/enricher-design.md#error-handling)). DLQ messages represent events that **will not be enriched without manual intervention** — the affected records keep their base fields (written by Scribe) but never get their LLM summaries (`root_cause_summary`, `pull_request_summary`, `issue_summary`, etc.). This is the durable-failure counterpart to the transient processing failures that precede DLQ growth. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to read, redrive, and delete messages on the Enricher DLQ and main queue
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/dfpxtsorldou8f?orgId=1)

## Steps

1. Confirm the current DLQ depth.

   ```promql
   max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enr-.*production.*dlq.*"})
   ```

2. Identify the dominant DLQ error type (the Enricher labels DLQ routing by `error_type`).

   ```promql
   sum by (error_type) (rate(matik_enricher_dlq_messages_total{deployment_environment="production"}[1h]))
   ```

3. Look up the DLQ and main queue URLs.

   ```bash
   aws sqs list-queues --queue-name-prefix matik-enr
   ```

   TODO: confirm the concrete production main-queue and DLQ URLs/regions (`ENRICHER_QUEUE_URL` / `ENRICHER_DLQ_URL` rendered into `matik-enricher-config.yml`).

4. Read a sample DLQ message without deleting it to identify the failure.

   ```bash
   aws sqs receive-message --queue-url "$ENRICHER_DLQ_URL" --max-number-of-messages 10 --visibility-timeout 0 --attribute-names All --message-attribute-names All
   ```

5. Correlate the failure with the Enricher logs around when the messages were dead-lettered.

   ```bash
   kubectl -n matik-production logs -l app=matik-enricher-production --since=6h | grep -iE "dlq|content_filter|badrequest|malformed|error"
   ```

6. Classify the cause and remediate:

   - **MalformedMessageError** — a producer (Historian/Chronicler) emitted a bad payload; fix the producer. Replaying as-is will fail again.
   - **ContentFilteredError / FacadeBadRequestError** — the same input will always be filtered/rejected; replay is futile. These are expected drops, not always actionable — decide whether to discard.
   - **Max-retries-exceeded (was retryable)** — a dependency outage (Facade/Scribe/API) exhausted retries; once recovered, these are safe to replay.

7. Confirm no new processing failures are still occurring before replay (an active fault will refill the DLQ).

   ```promql
   sum(rate(matik_enricher_messages_processed_total{status="failed", deployment_environment="production"}[5m]))
   ```

8. Once the cause is fixed, redrive the eligible DLQ messages back onto the main Enricher queue.

   TODO: confirm the canonical redrive mechanism (AWS SQS console "Start DLQ redrive", an `sqspurger`/redrive script, or a manual `send-message` + `delete-message` loop). Do not delete DLQ messages until they are confirmed reprocessed. Do not blindly redrive `ContentFilteredError`/`BadRequest` messages — they will fail again.

9. Confirm the redriven messages now enrich successfully (no new DLQ arrivals).

## Verify

Confirm the DLQ has drained to empty (or to only the expected non-actionable content-filter drops).

```promql
max(aws_sqs_approximate_number_of_messages_visible{queue_name=~"matik-enr-.*production.*dlq.*"})
```

Expected output: depth `0`; the alert clears in #matik-alerts.

Confirm replayed messages processed successfully.

```promql
sum(rate(matik_enricher_messages_processed_total{status="success", deployment_environment="production"}[5m]))
```

Expected output: a processed-rate bump matching the redriven volume, with no corresponding failure-rate rise.

## Escalate

| Condition | Action |
| --- | --- |
| Cannot identify the failure cause from DLQ messages + logs | Post in #matik-internal with a sample message |
| DLQ keeps refilling after remediation | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Failure caused by `llm-fusion-hub` or `matik-api` | Page the owning team for that dependency |
| Malformed payloads traced to a producer | Coordinate with the Historian / Chronicler owner to fix the producer |
| Redrive mechanism unavailable / unclear | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

DLQ remediation does not deploy code, so there is no deploy to roll back. If the underlying failure was introduced by a release, roll that back before redriving.

```bash
kubectl -n matik-production rollout undo deploy/matik-enricher-production
```

TODO: add the Matik Enricher Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `enricher/enricher_dlq_non_empty.ts` (Telescope/CAWS monitor `enricher_dlq_non_empty_production`):

```promql
max(aws_sqs_approximate_number_of_messages_visible{quantile="1", queue_name=~"matik-enr-.*production.*dlq.*"})
```

- Recording rule: `matik:enricher_dlq_depth:max`
- Metric: `aws_sqs_approximate_number_of_messages_visible` (CloudWatch SQS depth via the `cloudwatch` tenant; matches only the `dlq` queues). Not a Matik OTel metric. The Enricher also emits `matik_enricher_dlq_messages_total` (by `error_type`) for cause classification.
- Threshold / window: `>= 1` message, `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Content filtered / bad request (non-retryable)** — Facade returned a 400 or `finish_reason: "content_filter"`; the same input always fails, so it is dead-lettered immediately. Often expected, not always actionable. Confirm via `error_type` (step 2).
- **Malformed message (non-retryable)** — a producer emitted an unparseable payload or unknown `source_type`. Confirm by reading the DLQ message; fix the producer.
- **Persistent dependency failure (was retryable)** — repeated Facade/Scribe/API errors exhausted `maxReceiveCount`. Confirm via logs around the dead-letter time; safe to redrive once recovered.
- **Bug introduced by a deploy** — a regression failing a class of messages. Confirm by correlating dead-letter timing with rollout history.

### Related docs

- [Enricher Design — Error Handling](../architecture/enricher-design.md#error-handling)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Queue Depth Runaway](enricher-queue-depth-runaway.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
