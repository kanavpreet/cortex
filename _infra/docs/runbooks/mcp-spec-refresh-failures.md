# Matik MCP Spec Refresh Failures

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `mcp_spec_refresh_failures_production`
- Symptoms: MCP OpenAPI spec refreshes are failing; the exposed tool set may silently drift or break.

## Impact

The MCP server generates its tool definitions mechanically from the Matik API's `/openapi.json`, refreshing periodically (`MCP_SPEC_REFRESH_INTERVAL_MINUTES`, default 5) so it picks up new/changed API endpoints without a restart (per [MCP server architecture](../architecture/matik-mcp-server.md)). When refreshes fail, the server **serves the last good (stale) spec** — so there is no immediate client-facing error, but the tool set drifts from the API: newly added tools won't appear, and removed/renamed endpoints will produce `unknown_tool` errors when called (surfacing as [Tool Call Error Rate](mcp-tool-call-error-rate.md)). This alert is the early signal *before* that drift bites. The spec source is `matik-api-production` (the MCP server's only dependency). See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik MCP Dashboard](https://grafana.a.musta.ch/goto/bfpyu51jw3z0ge?orgId=1)

## Steps

1. Confirm the spec-refresh failure rate.

   ```promql
   sum(rate(matik_mcp_spec_refreshes_total{deployment_environment="production", status=~"error"}[5m]))
   ```

2. Inspect the MCP server logs for the refresh error detail.

   ```bash
   kubectl -n matik-production logs -l app=matik-mcp-production --tail=200 | grep -iE "spec|openapi|refresh|fetch|parse"
   ```

3. Check whether the Matik API (the spec source) is healthy and serving `/openapi.json`.

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s -o /dev/null -w "%{http_code}" localhost:8080/openapi.json
   ```

4. Confirm whether the failure is connectivity (API unreachable / 5xx) vs. parsing (spec present but invalid/breaking change).

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
   ```

5. List recent deployments of the API (a spec change can break parsing) and the MCP server.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-api-production
   kubectl -n matik-production rollout history deploy/matik-mcp-production
   ```

6. If a recent API change introduced a breaking/invalid spec, roll back the API (see [Rollback](#rollback)).

7. If the API is unreachable or erroring, fix the API first — follow the [API runbooks](api-availability-low.md). Refreshes resume automatically once the API serves a valid spec.

8. If the MCP server is wedged on a stale spec after the API recovers, restart it to force a clean refresh.

   ```bash
   kubectl -n matik-production rollout restart deploy/matik-mcp-production
   ```

## Verify

Confirm refreshes are succeeding again.

```promql
sum(rate(matik_mcp_spec_refreshes_total{deployment_environment="production", status=~"error"}[5m]))
```

Expected output: value at `0`; the alert clears in #matik-alerts.

Confirm healthy refresh outcomes (`success` or `unchanged`) are being recorded.

```promql
sum by (status) (rate(matik_mcp_spec_refreshes_total{deployment_environment="production"}[5m]))
```

Expected output: a steady `success`/`unchanged` rate with no `error`.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Matik API unreachable or serving an invalid spec | Follow [API runbooks](api-availability-low.md); coordinate with Ops Eng |
| Tool drift already causing `unknown_tool` errors | Follow [Tool Call Error Rate](mcp-tool-call-error-rate.md) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If refresh failures began after an API release that changed the OpenAPI spec, roll back the API.

```bash
kubectl -n matik-production rollout undo deploy/matik-api-production
```

If an MCP server release broke spec parsing, roll back the MCP server instead.

```bash
kubectl -n matik-production rollout undo deploy/matik-mcp-production
```

TODO: add the Matik MCP / API Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `mcp/mcp_spec_refresh_failures.ts` (Telescope/CAWS monitor `mcp_spec_refresh_failures_production`):

```promql
sum(rate(matik_mcp_spec_refreshes_total{deployment_environment="production", status!~"success|unchanged"}[5m]))
```

- Recording rule: `matik:mcp_spec_refresh_failures:sum`
- Metric: `matik_mcp_spec_refreshes_total` (Counter; OpenAPI spec refresh attempts, `status` = `success`/`unchanged`/`error`, see [MCP Server Metrics](../observability/metrics.md#mcp-server-metrics)). The alert treats any status that is **not** `success` or `unchanged` as a failure, so it is robust to the exact error label string.
- Threshold / window: `> 0` failures/s, `for: 15m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **API unreachable / erroring** — the MCP server cannot fetch `/openapi.json` from `matik-api-production` (5xx, timeout, network). Confirm via `/openapi.json` status and `/ready` (steps 3–4).
- **Invalid / breaking spec change** — an API deploy produced a spec the MCP server cannot parse or that fails version validation (the design says to "fail loudly on breaking changes"). Confirm via parse errors in logs and API rollout history.
- **Bad MCP server deployment** — a regression in the spec fetcher/parser. Confirm via MCP rollout history.

Per the architecture's error-handling rule, a spec fetch failure causes the server to **serve the stale spec if available** and alert on staleness — so clients keep working on the old tool set until drift causes `unknown_tool` errors.

### Related docs

- [MCP Server Architecture — Tool Registration](../architecture/matik-mcp-server.md)
- [Metrics Reference](../observability/metrics.md#mcp-server-metrics)
- Related runbooks: [Tool Call Error Rate](mcp-tool-call-error-rate.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
