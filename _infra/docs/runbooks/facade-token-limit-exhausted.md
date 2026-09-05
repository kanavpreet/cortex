# Matik Facade Token Limit Exhausted

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `facade_token_limit_exhausted_production`
- Symptoms: The LLM Facade has no remaining API tokens for a model; enrichment calls will fail until the token limit resets.

## Impact

Facade is Matik's LLM client routing through Airbnb's **LLM Fusion Hub** (`llm-fusion-hub-production`) — not a Matik-deployed service. When the per-model **token** rate limit hits zero, all Facade calls for that model fail (HTTP 429) until the window resets. The callers — primarily the **Enricher** and **Enigmatologist** — then fail their LLM calls: Enricher messages retry with backoff and risk DLQ growth; Enigmatologist correlations fail. Tokens are driven by **prompt + completion size**, so large inputs (long incident summaries, PR bodies, JIRA comments) burn the budget faster even at modest call rates. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace (to read caller logs/config)
- [ ] Grafana access for the [Matik Enricher & Facade Dashboard](https://grafana.a.musta.ch/goto/ffpypazzw4etcc?orgId=1)
- [ ] LLM Fusion Hub status/contact for provider-side quota

## Steps

1. Identify which model's token budget is exhausted.

   ```promql
   min by (model) (matik_facade_ratelimit_remaining_tokens{deployment_environment="production"})
   ```

2. Compare remaining vs. the configured token limit.

   ```promql
   min by (model) (matik_facade_ratelimit_limit_tokens{deployment_environment="production"})
   ```

3. Check token consumption (prompt vs. completion) to see what is burning the budget.

   ```promql
   sum by (service) (rate(matik_facade_prompt_tokens_total{deployment_environment="production"}[5m]))
   sum by (service) (rate(matik_facade_completion_tokens_total{deployment_environment="production"}[5m]))
   ```

4. Check whether a traffic surge or unusually large inputs are driving demand.

   ```promql
   sum(rate(matik_facade_calls_total{deployment_environment="production"}[5m]))
   ```

5. Confirm Matik is using its **dedicated** model deployment (`matik-production-gpt-5`), not the shared `gpt-5` deployment, via the `model` label.

6. Inspect the Enricher logs for 429 / token-limit messages and confirm backoff is engaging.

   ```bash
   kubectl -n matik-production logs -l app=matik-enricher-production --tail=200 | grep -iE "429|token|rate limit|high demand"
   ```

7. Reduce token demand. Options, in order of preference:
   - Lower caller concurrency (`max_concurrent_llm_calls` in `matik-enricher-config.yml`; total concurrency is `max_concurrent_llm_calls × replicas`) and/or scale in replicas.
   - Reduce prompt size if inputs are unusually large (the `general_prompt` plus content; PII scrubbing already trims some).

   TODO: confirm the safe `max_concurrent_llm_calls` value and the canonical mechanism to change config.

8. List recent deployments to check whether a prompt change, model switch, or concurrency bump triggered the exhaustion.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-enricher-production
   ```

9. If a recent Matik deploy raised token demand, roll it back (see [Rollback](#rollback)).

10. If the token limit is genuinely too low for steady-state load, request a token-quota increase from the LLM Fusion Hub team (see [Escalate](#escalate)).

## Verify

Confirm remaining tokens have recovered above zero.

```promql
min by (model) (matik_facade_ratelimit_remaining_tokens{deployment_environment="production"})
```

Expected output: a positive remaining-tokens value sustained for at least 5 minutes; the alert clears in #matik-alerts.

Confirm callers recover (Enricher backoffs subside).

```promql
sum(rate(matik_enricher_backoff_total{deployment_environment="production"}[5m]))
```

Expected output: a falling backoff rate trending to zero.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Token limit too low for steady-state load | Request a token-quota / Provisioned Throughput increase from the LLM Fusion Hub team (TODO: confirm channel) |
| Sharing pooled `gpt-5` deployment | Switch the caller to Matik's dedicated `matik-production-gpt-5` deployment |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.
- LLM Fusion Hub: TODO — confirm quota-request / escalation channel.

## Rollback

There is no `matik-facade` service. If exhaustion began after a Matik change that raised token demand (larger prompts, higher `max_concurrent_llm_calls`, more replicas, or a model switch), roll back that caller.

```bash
kubectl -n matik-production rollout undo deploy/matik-enricher-production
```

TODO: add the Matik Enricher / Enigmatologist Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `facade/facade_token_limit_exhausted.ts` (Telescope/CAWS monitor `facade_token_limit_exhausted_production`):

```promql
min by (model) (matik_facade_ratelimit_remaining_tokens{deployment_environment="production"})
```

- Recording rule: `matik:facade_ratelimit_remaining_tokens:min`
- Metric: `matik_facade_ratelimit_remaining_tokens` (Gauge; remaining token budget reported by the provider, per `model`, see [Facade Metrics](../observability/metrics.md#facade-metrics)).
- Threshold / window: `<= 0`, `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Large inputs** — long incident summaries, PR bodies, or JIRA comment aggregates burn tokens fast even at modest call rates. Confirm via the prompt-token rate (step 3) being high relative to the call rate.
- **Demand surge** — a large Historian backfill or webhook burst multiplies total token usage. Confirm via the call rate (step 4).
- **Shared deployment contention** — using the pooled `gpt-5` deployment instead of `matik-production-gpt-5` causes throttling under load. Confirm via the `model` label.
- **Concurrency too high** — `max_concurrent_llm_calls × replicas` drives token throughput past the budget. Confirm by correlating with caller rollout history.
- **Quota too low** — steady-state load legitimately exceeds the provisioned token limit. Confirm by comparing the limit gauge (step 2) against sustained demand → request an increase.

### Related docs

- [Facade / LLM Client Guide](../development/facade.md)
- [Enricher Design](../architecture/enricher-design.md)
- [Metrics Reference](../observability/metrics.md#facade-metrics)
- Related runbooks: [Call Error Rate High](facade-call-error-rate.md), [Enricher Queue Depth Runaway](enricher-queue-depth-runaway.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
