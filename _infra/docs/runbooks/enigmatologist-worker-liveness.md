# Matik Enigmatologist Worker Liveness

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `enigmatologist_worker_liveness_production`
- Symptoms: The Enigmatologist has stopped polling SQS; the correlation worker appears dead or crash-looping.

## Impact

The Enigmatologist is a long-running SQS polling worker (`python -m enigmatologist`) that consumes correlation requests, runs the reliability correlation engine, and persists results via the Matik API (per [enigmatologist architecture](../architecture/enigmatologist/enigmatologist.md)). When polling stops, **event correlation halts entirely** and the queue backs up silently — depth alerts only fire much later, so this liveness alert is the early signal. The worker depends on `matik-api-production` (read related data + persist results) and `llm-fusion-hub-production` (LLM correlation). See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] AWS access to inspect the Enigmatologist SQS queue and IAM role
- [ ] Grafana access for the [Matik SQS & Messaging Dashboard](https://grafana.a.musta.ch/goto/afpxtb0zve8zka?orgId=1)

## Steps

1. Check the worker pod status and restart count for crash loops.

   ```bash
   kubectl -n matik-production get pods -l app=matik-enigmatologist-production
   ```

2. Inspect the logs for crash tracebacks or the startup sequence.

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --tail=200
   ```

3. If the pod is restarting, inspect the previous container's logs for the crash cause.

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --previous --tail=200
   ```

4. Confirm the poll rate is actually zero.

   ```promql
   sum(rate(matik_sqs_poll_total{service="enigmatologist", deployment_environment="production"}[5m]))
   ```

5. Check for SQS connectivity or IAM errors in the logs (the poll loop logs `SQS poll error, retrying`).

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --tail=200 | grep -iE "sqs poll error|accessdenied|not authorized|credential|config"
   ```

6. List recent deployments to check whether the worker died after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-enigmatologist-production
   ```

7. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

8. If the pod is wedged (running but not polling) or crash-looping on a transient cause, restart the deployment.

   ```bash
   kubectl -n matik-production rollout restart deploy/matik-enigmatologist-production
   ```

9. If SQS is unreachable or the IAM role lost permissions, escalate to the SQS/IAM owner (see [Escalate](#escalate)).

## Verify

Confirm the worker is polling again.

```promql
sum(rate(matik_sqs_poll_total{service="enigmatologist", deployment_environment="production"}[5m]))
```

Expected output: a non-zero, steady poll rate (the counter fires every cycle, including idle ones); the alert clears in #matik-alerts.

Confirm the pod is stable.

```bash
kubectl -n matik-production get pods -l app=matik-enigmatologist-production
```

Expected output: pod `Running`, ready, with no new restarts.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| SQS unreachable or IAM `AccessDenied` | Escalate to the SQS queue / IAM role owner |
| Crash caused by `matik-api` or `llm-fusion-hub` unavailability | Page the owning team for that dependency |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If the worker died after a release, roll back the most recent rollout.

```bash
kubectl -n matik-production rollout undo deploy/matik-enigmatologist-production
```

TODO: add the Matik Enigmatologist Spinnaker pipeline link for the canonical rollback path.

## Appendix

### Alert definition

Defined in `enigmatologist/enigmatologist_worker_liveness.ts` (Telescope/CAWS monitor `enigmatologist_worker_liveness_production`):

```promql
sum(rate(matik_sqs_poll_total{service="enigmatologist", deployment_environment="production"}[5m]))
```

- Recording rule: `matik:enigmatologist_poll_rate:sum`
- Metric: `matik_sqs_poll_total` (Counter; total SQS poll attempts — fires every cycle to keep the series alive during idle periods, see [SQS Metrics](../observability/metrics.md)). Recorded in the poll loop in `matik/enigmatologist/main.py`.
- Threshold / window: `< 0.0001` polls/s, `for: 15m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Crash loop on startup** — bad config, failed client init (`matik-api` / LLM), or a code regression. Confirm via `--previous` logs (step 3) and rollout history.
- **Pod killed / not scheduled** — OOM kill or eviction. Confirm via pod status and restart count.
- **SQS connectivity / IAM failure** — the worker cannot reach SQS or lost `sqs:ReceiveMessage`; the poll loop logs and retries after `sqs_poll_error_delay`. Confirm via the SQS-error log grep (step 5).
- **Worker wedged** — the loop stalled despite a healthy-looking pod. Confirm via zero poll rate with a running pod; mitigate with a restart.

### Related docs

- [Enigmatologist Architecture](../architecture/enigmatologist/enigmatologist.md)
- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Queue Depth Runaway](enigmatologist-queue-depth-runaway.md), [DLQ Non-Empty](enigmatologist-dlq-non-empty.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
