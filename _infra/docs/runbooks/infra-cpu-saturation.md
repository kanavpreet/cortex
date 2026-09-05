# Matik Container CPU Saturation

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `cpu_saturation_production`
- Symptoms: A Matik container is using over 95% of its requested CPU and is being throttled.

## Impact

A container saturating its CPU request is throttled by the kernel, so request/processing latency for the affected component climbs. The alert is per `pod`/`container`, so the labels name the offender. Impact depends on which service: a saturated `matik-api` raises API latency for every caller (historians, enricher, enigmatologist, mcp); a saturated worker (`enricher`, `enigmatologist`, `chronicler`) slows ingest/enrichment and can grow SQS queue depth. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)

## Steps

1. Identify the saturated pod/container from the alert labels (or via the metric).

   ```promql
   topk(5, sum by (pod, container) (rate(container_cpu_usage_seconds_total{namespace=~"matik.*", cellset="prod-use1", container!=""}[5m])) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="cpu"}) * 100)
   ```

2. Map the pod to its service and check pod health / resource usage.

   ```bash
   kubectl -n matik-production top pods --sort-by=cpu
   ```

3. Inspect the saturated pod's logs for a hot loop, retry storm, or traffic surge.

   ```bash
   kubectl -n matik-production logs <pod-name> --tail=200
   ```

4. Check whether load is driving it (traffic surge or queue backlog) vs. a regression.

   ```bash
   kubectl -n matik-production get deploy -l app=<service> -o wide
   ```

5. List recent deployments to check whether saturation began after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/<service>
   ```

6. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

7. If the service is genuinely under-provisioned, raise its CPU request/limit or scale out.

   ```bash
   kubectl -n matik-production scale deploy/<service> --replicas=<N+1>
   ```

   TODO: confirm the canonical mechanism to change CPU requests/limits and replicas (kube-gen `Env.Params` + redeploy vs. HPA).

## Verify

Confirm CPU saturation has dropped below threshold for the affected container.

```promql
max(sum by (pod, container) (rate(container_cpu_usage_seconds_total{namespace=~"matik.*", cellset="prod-use1", container!=""}[5m])) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="cpu"}) * 100)
```

Expected output: value below `95` (percent) sustained for at least 10 minutes; the alert clears in #matik-alerts.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Saturation is `matik-api` and degrading callers | See [API runbooks](api-p95-latency-high.md) |
| Caused by a queue backlog | Follow the relevant worker runbook ([Enricher](enricher-queue-depth-runaway.md) / [Enigmatologist](enigmatologist-queue-depth-runaway.md)) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If saturation began after a release, roll back the affected service.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_cpu_saturation.ts` (Telescope/CAWS monitor `cpu_saturation_production`):

```promql
sum by (pod, container) (rate(container_cpu_usage_seconds_total{namespace=~"matik.*", cellset="prod-use1", container!=""}[5m])) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="cpu"}) * 100
```

- Recording rule: `matik:container_cpu_saturation_pct:max`
- Metrics: `container_cpu_usage_seconds_total` / `kube_pod_container_resource_requests` (kube-state, `k8s` tenant). Production is scoped via `cellset="prod-use1"` — kube-state metrics carry no `deployment_environment` label.
- Threshold / window: `> 95` (percent of CPU request), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Traffic / backlog surge** — more requests or a large queue backlog drives CPU. Confirm via load metrics and queue depth.
- **Under-provisioned request** — the CPU request is set too low for steady-state load. Confirm via sustained saturation without a load spike.
- **Bad recent deployment** — a regression (hot loop, inefficient code path). Confirm by correlating with rollout history.
- **Noisy neighbor / node pressure** — less likely; confirm via node-level metrics.

### Related docs

- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Memory Saturation](infra-memory-saturation.md), [Pod Restart Storm](infra-pod-restart-storm.md), [OOMKilled Container](infra-oom-killed.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
