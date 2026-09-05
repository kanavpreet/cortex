# Matik Facade Call Error Rate High

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `facade_call_error_rate_production`
- Symptoms: More than 1-in-10 LLM Facade calls are failing; enrichment quality and throughput are degraded.

## Impact

Facade is Matik's LLM client (`common/clients/facade_client.py`), routing through Airbnb's **LLM Fusion Hub** (`llm-fusion-hub-production`) to Azure/OpenAI GPT models — it is **not a Matik-deployed service**. The `matik_facade_*` metrics are emitted by the Matik services that call it: primarily the **Enricher** and the **Enigmatologist** (both depend on `llm-fusion-hub-production` per `_infra/mesh.yml`). A high error rate degrades enrichment (missing `root_cause_summary`, `pull_request_summary`, `issue_summary`, etc.) and correlation, and cascades into **Enricher retries/backoff and DLQ growth** and **Enigmatologist processing failures**. There is no `matik-facade` pod to restart — mitigation is on the callers, the provider, and config. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace (to read caller logs)
- [ ] Grafana access for the [Matik Enricher & Facade Dashboard](https://grafana.a.musta.ch/goto/ffpypazzw4etcc?orgId=1)
- [ ] LLM Fusion Hub status/contact for provider-side issues

## Steps

1. Confirm the current error ratio and break it down by model and operation.

   ```promql
   sum by (model, operation) (rate(matik_facade_call_errors_total{deployment_environment="production"}[5m])) / sum by (model, operation) (rate(matik_facade_calls_total{deployment_environment="production"}[5m]))
   ```

2. Identify which caller is seeing the errors (Enricher vs. Enigmatologist) via the `service` label.

   ```promql
   sum by (service) (rate(matik_facade_call_errors_total{deployment_environment="production"}[5m]))
   ```

3. Inspect the Enricher logs for the Facade error detail (status, message).

   ```bash
   kubectl -n matik-production logs -l app=matik-enricher-production --tail=200 | grep -iE "facade|llm|timeout|429|5[0-9][0-9]|content_filter|badrequest"
   ```

4. Inspect the Enigmatologist logs for the same.

   ```bash
   kubectl -n matik-production logs -l app=matik-enigmatologist-production --tail=200 | grep -iE "facade|bedrock|llm|timeout|429|5[0-9][0-9]"
   ```

5. Check whether the errors are rate/token-limit driven (often the root cause of a 429 spike).

   ```promql
   min by (model) (matik_facade_ratelimit_remaining_requests{deployment_environment="production"})
   min by (model) (matik_facade_ratelimit_remaining_tokens{deployment_environment="production"})
   ```

6. Check the LLM Fusion Hub status page / health for a provider-side incident.

   TODO: add the LLM Fusion Hub status page / health-check link and the owning team's escalation channel.

7. Confirm Matik is using its **dedicated** model deployment (`matik-production-gpt-5`), not the shared `gpt-5` deployment, by checking the `model` label in step 1 and the Enricher/Enigmatologist config.

8. List recent deployments of the calling services to check whether errors began after a Matik change (new prompt, model, or concurrency bump).

   ```bash
   kubectl -n matik-production rollout history deploy/matik-enricher-production
   kubectl -n matik-production rollout history deploy/matik-enigmatologist-production
   ```

9. If a recent Matik deploy is implicated, roll back the affected caller (see [Rollback](#rollback)).

10. If the errors are token-limit driven, follow [Token Limit Exhausted](facade-token-limit-exhausted.md).

11. If the provider (Fusion Hub) is unhealthy, escalate to its owning team (see [Escalate](#escalate)); retryable Enricher messages self-heal via backoff once it recovers.

## Verify

Confirm the error ratio has returned below the alert threshold.

```promql
sum(rate(matik_facade_call_errors_total{deployment_environment="production"}[5m])) / sum(rate(matik_facade_calls_total{deployment_environment="production"}[5m])) * 100
```

Expected output: value below `10` (percent) sustained for at least 5 minutes; the alert clears in #matik-alerts.

Confirm downstream callers recover (Enricher backoffs subside).

```promql
sum(rate(matik_enricher_backoff_total{deployment_environment="production"}[5m]))
```

Expected output: a falling backoff rate trending to zero.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| LLM Fusion Hub provider-side outage / errors | Page the LLM Fusion Hub owning team (TODO: confirm channel) |
| Token limit exhausted | Follow [Token Limit Exhausted](facade-token-limit-exhausted.md) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.
- LLM Fusion Hub: TODO — confirm provider status page and escalation channel.

## Rollback

There is no `matik-facade` service. If errors began after a Matik change to a calling service (e.g. a new prompt, a switched model, or a raised `max_concurrent_llm_calls`), roll back that caller.

```bash
kubectl -n matik-production rollout undo deploy/matik-enricher-production
```

TODO: add the Matik Enricher / Enigmatologist Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `facade/facade_call_error_rate.ts` (Telescope/CAWS monitor `facade_call_error_rate_production`):

```promql
sum(rate(matik_facade_call_errors_total{deployment_environment="production"}[5m])) / sum(rate(matik_facade_calls_total{deployment_environment="production"}[5m])) * 100
```

- Recording rule: `matik:facade_call_error_rate:ratio`
- Metrics: `matik_facade_call_errors_total` / `matik_facade_calls_total` (Counters; labels `service`, `client`, `model`, `operation`, see [Facade Metrics](../observability/metrics.md#facade-metrics)).
- Threshold / window: `> 10` (percent), `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Rate / token limit exhaustion** — 429s when the per-model request or token budget is spent. Confirm via the remaining-limit gauges (step 5) → [Token Limit](facade-token-limit-exhausted.md). Most common when Matik is on the shared `gpt-5` deployment instead of its dedicated one.
- **Provider (Fusion Hub) outage / 5xx** — upstream Azure/OpenAI or Fusion Hub degradation. Confirm via the status page and `service`-agnostic error spike across all callers.
- **Matik-side regression** — a deploy raised concurrency, changed the model, or enlarged prompts (more tokens → more 429/400). Confirm by correlating with caller rollout history.
- **Content-filter / bad-request spikes** — input or output policy violations return 400/`content_filter`; these are non-retryable and also feed the Enricher DLQ. Confirm via the error log grep (step 3).

Facade timeouts, 429s, and 5xx are retryable in the Enricher (visibility-timeout backoff 10/30/60s); 400 and content-filter are non-retryable → DLQ. `send_message_with_retry` adds 3 retries (10/20/40s) at the client level.

### Related docs

- [Facade / LLM Client Guide](../development/facade.md)
- [Enricher Design](../architecture/enricher-design.md)
- [Metrics Reference](../observability/metrics.md#facade-metrics)
- Related runbooks: [Token Limit Exhausted](facade-token-limit-exhausted.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
