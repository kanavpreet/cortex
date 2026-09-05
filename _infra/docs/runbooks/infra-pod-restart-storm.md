# Matik Pod Restart Storm

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `pod_restart_storm_production`
- Symptoms: Five or more Matik pod restarts in the last 15 minutes; a crash loop may be in progress.

## Impact

Rapid restarts mean a Matik service is crash-looping and is not reliably serving traffic or processing work. The alert sums restarts across all `matik-*` apps, so triage starts by finding which pod(s) are restarting. Impact depends on the service: `matik-api` crash-looping degrades every caller; a crash-looping worker (`enricher`, `enigmatologist`, `chronicler`) halts ingest/enrichment/correlation. Common drivers are OOMKills (see [OOMKilled Container](infra-oom-killed.md)), failed readiness/liveness probes, or a startup crash from bad config or a failed dependency. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik Infrastructure Dashboard](https://grafana.a.musta.ch/goto/bfpytfo3egmiob?orgId=1)

## Steps

1. Identify which pods are restarting and how many times.

   ```bash
   kubectl -n matik-production get pods --sort-by=.status.containerStatuses[0].restartCount | tail -20
   ```

2. Confirm which app dominates the restart count via the metric.

   ```promql
   sum by (app) (increase(kube_pod_container_status_restarts_total{app=~"matik-.*", cellset="prod-use1"}[15m]))
   ```

3. Inspect the crashing pod's current logs for the startup/runtime error.

   ```bash
   kubectl -n matik-production logs <pod-name> --tail=200
   ```

4. Inspect the previous container's logs for the crash cause (panic, traceback, exit).

   ```bash
   kubectl -n matik-production logs <pod-name> --previous --tail=200
   ```

5. Describe the pod to see the termination reason and probe failures.

   ```bash
   kubectl -n matik-production describe pod <pod-name>
   ```

6. If the termination reason is `OOMKilled`, follow [OOMKilled Container](infra-oom-killed.md).

7. Check whether a dependency the pod needs at startup is down (e.g. `matik-api` for workers, MySQL for the API readiness probe).

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
   ```

8. List recent deployments to check whether the crash loop began after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/<service>
   ```

9. If a recent deploy is implicated (bad config, regression), roll it back (see [Rollback](#rollback)).

10. If the crash is from a transient dependency failure, fix/await that dependency; the pod recovers once it is reachable.

## Verify

Confirm restarts have stopped.

```promql
sum(increase(kube_pod_container_status_restarts_total{app=~"matik-.*", cellset="prod-use1"}[15m]))
```

Expected output: below `5` and trending to zero; the alert clears in #matik-alerts.

Confirm the affected pods are stable.

```bash
kubectl -n matik-production get pods -l app=<service>
```

Expected output: pods `Running`, ready, restart counts no longer climbing.

## Escalate

| Condition | Action |
| --- | --- |
| Crash loop not stopped in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Caused by OOMKills | Follow [OOMKilled Container](infra-oom-killed.md) |
| Caused by a down dependency (MySQL / `matik-api`) | Follow the relevant dependency runbook; page that owner if external |
| Root cause unclear after initial triage | Post in #matik-internal with `--previous` logs |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If the crash loop began after a release, roll back the affected service.

```bash
kubectl -n matik-production rollout undo deploy/<service>
```

TODO: add the Matik Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `infrastructure/infra_pod_restart_storm.ts` (Telescope/CAWS monitor `pod_restart_storm_production`):

```promql
sum(increase(kube_pod_container_status_restarts_total{app=~"matik-.*", cellset="prod-use1"}[15m]))
```

- Recording rule: `matik:pod_restarts_15m:sum`
- Metric: `kube_pod_container_status_restarts_total` (kube-state, `k8s` tenant). Production scoped via `cellset="prod-use1"` and `app=~"matik-.*"`.
- Threshold / window: `>= 5` restarts in 15m, `for: 1m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **OOMKill crash loop** — repeated memory kills. Confirm via termination reason → [OOMKilled Container](infra-oom-killed.md).
- **Bad recent deployment** — broken config, failed import, or a startup regression. Confirm via `--previous` logs and rollout history.
- **Failed liveness/readiness probe** — the pod starts but never goes ready (e.g. API DB probe failing), so it is killed and restarted. Confirm via `describe pod`.
- **Startup dependency down** — a worker can't reach `matik-api`, or the API can't reach MySQL. Confirm via `/ready` and dependency health.

### Related docs

- [Metrics Reference](../observability/metrics.md)
- Related runbooks: [OOMKilled Container](infra-oom-killed.md), [Memory Saturation](infra-memory-saturation.md), [CPU Saturation](infra-cpu-saturation.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
