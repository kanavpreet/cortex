# Matik MCP Tool Call Error Rate High

**Type**: Incident Response

> AI Generated — generated via `/create-runbook`; verify commands/thresholds before relying on it in an incident. Delete this note once the runbook has been human-reviewed.

| | |
|---|---|
| **Last Reviewed** | TODO — set after a human review |
| **Reviewed By** | TODO |

> After reviewing this runbook, update the `runbook_url` on each alert listed under [When to Use](#when-to-use) (Telescope/CAWS) so it points at this doc's current URL/anchor.

## When to Use

- Alert: `mcp_tool_call_error_rate_production`
- Symptoms: More than 1-in-10 MCP tool calls are failing; clients cannot use Matik tools reliably.

## Impact

The MCP server is a **thin protocol gateway** that exposes Matik's data as tools to MCP clients (OpsBot, AirDiagnosis). It owns no business logic — it proxies every tool call to `matik-api-production` (its only dependency per `_infra/mesh.yml`) and performs mechanical HTTP→MCP error mapping (per [MCP server architecture](../architecture/matik-mcp-server.md)). So a high tool-call error rate almost always means **the Matik API is failing or unreachable**, not a bug in the MCP server itself. Clients depending on Matik via MCP experience degraded or broken functionality. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] Grafana access for the [Matik MCP Dashboard](https://grafana.a.musta.ch/goto/bfpyu51jw3z0ge?orgId=1)

## Steps

1. Confirm the error ratio and break it down by tool and error type.

   ```promql
   sum by (tool_name, error_type) (rate(matik_mcp_tool_call_errors_total{deployment_environment="production"}[5m])) / scalar(sum(rate(matik_mcp_tool_calls_total{deployment_environment="production"}[5m]))) * 100
   ```

2. Check the `error_type` label — `HTTPStatusError` points at the API; `unknown_tool` points at a tool-registry/spec problem (see [Spec Refresh Failures](mcp-spec-refresh-failures.md)).

3. Inspect the MCP server logs for the failing tool and the proxied API status.

   ```bash
   kubectl -n matik-production logs -l app=matik-mcp-production --tail=200 | grep -iE "error|tool|status|proxy|httpstatus"
   ```

4. Check whether the Matik API (the proxy target) is healthy — this is the most common root cause.

   ```bash
   kubectl -n matik-production exec deploy/matik-api-production -- curl -s localhost:8080/ready
   ```

5. Confirm whether the API itself is erroring (the MCP errors are usually a mirror of API 5xx).

   ```promql
   sum(rate(matik_http_requests_total{service="api", status=~"5..", deployment_environment="production"}[5m]))
   ```

6. Confirm the MCP server can reach the API (the `/health` probe verifies API reachability).

   ```bash
   kubectl -n matik-production get pods -l app=matik-mcp-production
   ```

7. List recent deployments of both the MCP server and the API to check whether errors began after a release.

   ```bash
   kubectl -n matik-production rollout history deploy/matik-mcp-production
   kubectl -n matik-production rollout history deploy/matik-api-production
   ```

8. If the API is the cause, follow the [API runbooks](api-availability-low.md) — fixing the API clears the MCP errors. If a recent MCP deploy is implicated, roll it back (see [Rollback](#rollback)).

## Verify

Confirm the tool-call error ratio has returned below threshold.

```promql
sum(rate(matik_mcp_tool_call_errors_total{deployment_environment="production"}[5m])) / sum(rate(matik_mcp_tool_calls_total{deployment_environment="production"}[5m])) * 100
```

Expected output: value below `10` (percent) sustained for at least 5 minutes; the alert clears in #matik-alerts.

Confirm successful tool calls are flowing.

```promql
sum(rate(matik_mcp_tool_calls_total{deployment_environment="production"}[5m]))
```

Expected output: a steady call rate with a low error fraction.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Page Matik on-call via PagerDuty (service name TODO — not in alert definition) |
| Matik API is the root cause | Follow [API runbooks](api-availability-low.md); coordinate with Ops Eng |
| `error_type` is `unknown_tool` (registry/spec drift) | Follow [Spec Refresh Failures](mcp-spec-refresh-failures.md) |
| Root cause unclear after initial triage | Post in #matik-internal |

- Slack: #matik-internal (team), #matik-alerts (alerts), #biztech-opseng-goalie (Ops Eng goalie)
- PagerDuty: TODO — no service configured in the alert definition; confirm with Ops Eng.

## Rollback

If errors began after an MCP server release, roll it back.

```bash
kubectl -n matik-production rollout undo deploy/matik-mcp-production
```

If the API is the cause, roll back the API instead (see [API runbooks](api-availability-low.md)).

TODO: add the Matik MCP / API Spinnaker pipeline links for the canonical rollback path.

## Appendix

### Alert definition

Defined in `mcp/mcp_tool_call_error_rate.ts` (Telescope/CAWS monitor `mcp_tool_call_error_rate_production`):

```promql
sum(rate(matik_mcp_tool_call_errors_total{deployment_environment="production"}[5m])) / sum(rate(matik_mcp_tool_calls_total{deployment_environment="production"}[5m])) * 100
```

- Recording rule: `matik:mcp_tool_call_error_rate:ratio`
- Metrics: `matik_mcp_tool_call_errors_total` / `matik_mcp_tool_calls_total` (Counters; labels `service`, `tool_name`, `status`, `error_type`, see [MCP Server Metrics](../observability/metrics.md#mcp-server-metrics)). These are transport-level metrics from the MCP server, not the API.
- Threshold / window: `> 10` (percent), `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Matik API failing / unreachable** — the MCP server proxies all calls to `matik-api-production`; API 5xx/timeouts surface as MCP `HTTPStatusError`. Confirm via `/ready` and the API 5xx rate (steps 4–5). This is the dominant cause given the thin-gateway design.
- **Tool registry / spec drift** — calls to a tool that no longer maps to an API route return `unknown_tool`. Confirm via `error_type` → [Spec Refresh Failures](mcp-spec-refresh-failures.md).
- **Bad recent deployment** — an MCP server regression in proxying or error mapping. Confirm via MCP rollout history.
- **API auth/rate-limit responses** — `401/403/429` from the API are mapped through to MCP errors. Confirm via the API status codes in MCP logs.

### Related docs

- [MCP Server Architecture](../architecture/matik-mcp-server.md)
- [Metrics Reference](../observability/metrics.md#mcp-server-metrics)
- Related runbooks: [Spec Refresh Failures](mcp-spec-refresh-failures.md), [API Availability Low](api-availability-low.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
