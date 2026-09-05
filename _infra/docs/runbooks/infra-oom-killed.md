# Matik OOMKilled Container

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `oom_killed_production`
- Symptoms: A Matik container was OOMKilled; the pod was forcibly restarted due to memory exhaustion.

## Impact

An OOMKill is an immediate, forced pod restart: in-flight requests/work are dropped at the moment of the kill. If the underlying cause persists, the pod **crash-loops** (OOMKill → restart → fill memory → OOMKill), taking the service out of reliable operation. For workers (`enricher`, `enigmatologist`, `chronicler`) SQS/Kafka at-least-once semantics redeliver dropped messages, but repeated kills stall the pipeline; for `matik-api` it drops live requests. This is the post-kill counterpart to [Memory Saturation](infra-memory-saturation.md), which warns before the kill. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)

## Steps

1. Identify which pod(s) were OOMKilled.

   ```bash
   kubectl -n matik-production get pods -o json | jq -r '.items[] | select(.status.containerStatuses[]?.lastState.terminated.reason=="OOMKilled") | .metadata.name'
   ```

2. Confirm the OOMKill count and the affected app via the metric.

   ```promql
   sum by (app) (kube_pod_container_status_last_terminated_reason{app=~"matik-.*", reason="OOMKilled", cellset="prod-use1"})
   ```

3. Check restart counts to determine whether it is a one-off or a crash loop.

   ```bash
   kubectl -n matik-production get pods -l app=<service> -o wide
   ```

4. Inspect the previous (killed) container's logs for what it was doing before the kill.

   ```bash
   kubectl -n matik-production logs <pod-name> --previous --tail=200
   ```

5. Review the memory trend leading up to the kill (leak vs. spike).

   ```promql
   sum by (pod) (container_memory_working_set_bytes{namespace=~"matik.*", cellset="prod-use1", pod=~"<service>.*"})
   ```

6. List recent deployments to check whether the OOMKill began after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/<service>
   ```

7. If a recent deploy is implicated, roll it back to stop the crash loop (see [Rollback](#rollback)).

8. If the service legitimately needs more memory, raise its memory limit to stop recurrence.

   TODO: confirm the canonical mechanism to change the memory limit (kube-gen `Env.Params` + redeploy).

9. If a leak is suspected and not deploy-related, capture the evidence (step 4/5) for a code fix; a restart only buys time.

## Verify

Confirm no further OOMKills are occurring and the pod is stable.

```promql
sum(increase(kube_pod_container_status_last_terminated_reason{app=~"matik-.*", reason="OOMKilled", cellset="prod-use1"}[15m]))
```

Expected output: `0` new OOMKills; the alert clears in #matik-alerts.

Confirm the pod is running with a steady restart count.

```bash
kubectl -n matik-production get pods -l app=<service>
```

Expected output: pod `Running`, ready, restart count no longer climbing.

## Escalate

| Condition | Action |
| --- | --- |
| Crash loop not stopped in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Repeated OOMKills after a limit increase (suspected leak) | Roll back if deploy-related; post in #matik-internal with `--previous` logs |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If the OOMKill began after a release, roll back the affected service to stop the crash loop.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_oom_killed.ts` (Telescope/CAWS monitor `oom_killed_production`):

```promql
sum(kube_pod_container_status_last_terminated_reason{app=~"matik-.*", reason="OOMKilled", cellset="prod-use1"})
```

- Recording rule: `matik:oom_killed_containers:sum`
- Metric: `kube_pod_container_status_last_terminated_reason` (kube-state, `k8s` tenant; `reason="OOMKilled"`). Production scoped via `cellset="prod-use1"` and `app=~"matik-.*"`.
- Threshold / window: `>= 1`, `for: 1m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Memory limit too low for workload** — a legitimate spike (large batch, big payload) exceeded the limit. Confirm via memory trend and the work in `--previous` logs.
- **Memory leak** — unbounded growth eventually hits the limit, often repeatedly. Confirm via the climbing trend and recurrence.
- **Bad recent deployment** — a regression raised the footprint. Confirm by correlating with rollout history.
- **Sudden large input** — an oversized message/response. Confirm via `--previous` logs at the kill moment.

### Related docs

- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [Memory Saturation](infra-memory-saturation.md), [Pod Restart Storm](infra-pod-restart-storm.md), [CPU Saturation](infra-cpu-saturation.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
