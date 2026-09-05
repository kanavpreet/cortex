# Matik Container Memory Saturation

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `memory_saturation_production`
- Symptoms: A Matik container is using over 95% of its requested memory; an OOMKill is imminent.

## Impact

A container approaching its memory request/limit is about to be **OOMKilled** — when that happens the pod restarts and drops in-flight work (see [OOMKilled Container](infra-oom-killed.md) for the post-kill case). The alert is per `pod`/`container`, so the labels name the offender. Impact depends on the service: an OOMKill of `matik-api` drops in-flight requests; of a worker (`enricher`, `enigmatologist`, `chronicler`) interrupts message processing (SQS redelivers / Kafka redelivers, so at-least-once protects against loss but ingest stalls). This alert is the early warning to act **before** the kill. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)

## Steps

1. Identify the saturated pod/container from the alert labels (or via the metric).

   ```promql
   topk(5, sum by (pod, container) (container_memory_working_set_bytes{namespace=~"matik.*", cellset="prod-use1", container!=""}) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="memory"}) * 100)
   ```

2. Check pod memory usage and restart history.

   ```bash
   kubectl -n matik-production top pods --sort-by=memory
   ```

3. Determine whether memory is climbing steadily (leak) or spiking with load.

   ```promql
   sum by (pod) (container_memory_working_set_bytes{namespace=~"matik.*", cellset="prod-use1", pod=~"<service>.*"})
   ```

4. Inspect the pod's logs for large-batch processing or unbounded buffering.

   ```bash
   kubectl -n matik-production logs <pod-name> --tail=200
   ```

5. List recent deployments to check whether memory growth began after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/<service>
   ```

6. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

7. If memory grows unbounded with no load change (suspected leak), restart the pod to reclaim memory while investigating.

   ```bash
   kubectl -n matik-production rollout restart deploy/<service>
   ```

8. If the service legitimately needs more memory, raise its memory request/limit.

   TODO: confirm the canonical mechanism to change memory requests/limits (kube-gen `Env.Params` + redeploy).

## Verify

Confirm memory saturation has dropped below threshold for the affected container.

```promql
max(sum by (pod, container) (container_memory_working_set_bytes{namespace=~"matik.*", cellset="prod-use1", container!=""}) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="memory"}) * 100)
```

Expected output: value below `95` (percent) sustained for at least 10 minutes, with no OOMKill; the alert clears in #matik-alerts.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes or OOMKill occurs | Page Matik on-call via PagerDuty (service name TODO — not in alert definition); see [OOMKilled Container](infra-oom-killed.md) |
| Suspected memory leak after a release | Roll back and post in #matik-internal with the pod/heap detail |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If memory growth began after a release, roll back the affected service.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_memory_saturation.ts` (Telescope/CAWS monitor `memory_saturation_production`):

```promql
sum by (pod, container) (container_memory_working_set_bytes{namespace=~"matik.*", cellset="prod-use1", container!=""}) / sum by (pod, container) (kube_pod_container_resource_requests{namespace=~"matik.*", cellset="prod-use1", resource="memory"}) * 100
```

- Recording rule: `matik:container_memory_saturation_pct:max`
- Metrics: `container_memory_working_set_bytes` / `kube_pod_container_resource_requests` (kube-state, `k8s` tenant). Production scoped via `cellset="prod-use1"`.
- Threshold / window: `> 95` (percent of memory request), `for: 10m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Memory leak** — steady unbounded growth independent of load. Confirm via the climbing trend (step 3); mitigate with a restart, fix in code.
- **Large-batch processing** — a big Historian crawl or oversized payload spikes working set. Confirm via logs and correlation with batch activity.
- **Under-provisioned request** — the memory request is too low for steady-state. Confirm via sustained high usage without a leak pattern.
- **Bad recent deployment** — a regression increased memory footprint. Confirm by correlating with rollout history.

### Related docs

- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [OOMKilled Container](infra-oom-killed.md), [CPU Saturation](infra-cpu-saturation.md), [Pod Restart Storm](infra-pod-restart-storm.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
