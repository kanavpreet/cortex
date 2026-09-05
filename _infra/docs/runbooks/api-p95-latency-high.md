# Matik API p95 Latency High

**Type**: Incident Response

| | |
|---|---|
| **Last Reviewed** | 2026-07-21 |
| **Reviewed By** | @alfredo_moreira |

## When to Use

- Alert: `api_p95_latency_high_production`
- Symptoms: Production Matik API requests are slow; users and integrations see degraded response times.

## Impact

The API is responding slowly (p95 over 1 second). Callers across the platform — historians, enigmatologist, enricher, mcp server (per `_infra/mesh.yml`) — see degraded latency and may time out or back up. The enricher's hash-lookup path (`/v1/enrichment/hashes`) is latency-sensitive: slow responses delay LLM cache-hit checks and back up the enrichment pipeline. Unlike the 5xx/availability alerts, requests may still succeed — just slowly. See the [Appendix](#appendix) for likely causes.

## Prerequisites

- [ ] Access to [headlamp](https://headlamp.a.musta.ch/projects/matik)
- [ ] Access to git.musta.ch [Matik repo](https://git.musta.ch/airbnb/matik)
- [ ] Access to [k tool](https://developers.a.musta.ch/docs/default/component/edge-docs/team-docs/development/k-tool-reference), to be able to run it in Matik repo
- [ ] Access to [cell authentication tool](https://developers.a.musta.ch/docs/default/component/kube-system/runbooks/cellauth/)
- [ ] Access to [IAP Auth tool](https://developers.a.musta.ch/docs/default/component/iap-auth/)
- [ ] Access to [Grafana Watchpoint](https://grafana.a.musta.ch/a/watchpoint?wp=workspace[0]:logs/watchpoint-logs?from=now-30m&to=now&var-project=matik) for logs
- [ ] DB access if recovery touches the database — see [db-management](../operations/db-management.md). Password is stored in 1Password.
- [ ] Access to [Spinnaker](https://spinnaker.a.musta.ch/#/applications/matik/executions/01KY09E04JXN1VWVEGHW34XD52?stage=1&step=1&details=Job%20Status) to view Matik deployments
- [ ] Grafana access for the [Matik API & Chronicler Dashboard](https://grafana.a.musta.ch/goto/bfpxrjzby3ym8a?orgId=1)
- [ ] Access to [Matik team LDAP group](https://gandalf-lite.airbnb.tools/permissions/ldap_groups/group/matik) to be able to access Matik via Airmesh

## Steps

1. Inspect the API logs for slow operations or errors.

- Via [Grafana Watchpoint](https://grafana.a.musta.ch/a/watchpoint?wp=workspace[0]:logs/watchpoint-logs?container=matik-api&from=now-6h&kube_role=matik-api-production&to=now&var-project=matik)
- Via [Headlamp](https://headlamp.a.musta.ch/projects/matik/production/matik-api?cellset=prod-use1). There is an option to stream logs, by selecting a pod and then clicking the stream logs button.
- Via k tools by running the following commands in the project repo:

   ```bash
   k logs
   ```
And follow the interactive terminal prompts.
2. Identify which routes are slow by breaking p95 down by route. Use the `metrics/matik` tenant.

   ```promql
   histogram_quantile(0.95, sum by (le, path) (rate(matik_http_request_duration_seconds_bucket{service="api", deployment_environment="production"}[5m])))
   ```

3. Check whether requests are piling up (in-flight concurrency). Use the `metrics/matik` tenant.

   ```promql
   sum(matik_http_active_requests{service="api", deployment_environment="production"})
   ```

4. Check the database readiness probe to rule out slow MySQL.

   ```bash
   IAP_TOKEN="$(iap-auth https://api-matik.a.musta.ch)"
   curl -H "Proxy-Authorization: Bearer $IAP_TOKEN" https://api-matik.a.musta.ch/ready
   ```

   Expected output: `{"status": "ready", ... "checks": {"database": "ok"}}`.

   Check the [Infrastructure Dashboard](https://grafana.a.musta.ch/d/matik-infrastructure/matik-infrastructure-dashboard?orgId=1&from=now-1h&to=now&timezone=browser&var-cellset=prod%7Cunknown%7Cbigid-use1%7Cbraintrust-use1%7Cgalileo-use1%7Cprod-use1%7C&var-cell=$__all&var-environment=staging&refresh=30s) as well, and inspect the RDS roe for:

   - High/Unusual CPU utilization
   - High/Unusual DB connections
   - Any deadlocks reported

5. Check pod health and replica count for crash loops or unavailable pods, via [Headlamp](https://headlamp.a.musta.ch/projects/matik/production/matik-api?cellset=prod-use1)

6. List recent deployments to check whether the latency rise correlates with a release.

   1. Check the Spinnaker **default** [pipeline job](https://spinnaker.a.musta.ch/#/applications/matik/executions/01KY09E04JXN1VWVEGHW34XD52?stage=1&step=1&details=Job%20Status).
   2. Check [Git releases](https://git.musta.ch/airbnb/matik/releases)

7. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

8. If latency is load-driven and pods are healthy, we might need to increase the resources or increse the HPA configurations.

   - Currently only CPU is a the threshold at 50%. Configuration is maintained [here](https://git.musta.ch/airbnb/matik/blob/main/_infra/kube/apps/matik-api.yml).

   -  For emergency scaling needs leverage the [k tool](https://developers.a.musta.ch/docs/default/component/kube-gen/getting-started/k-tools/#scaling-lifecycle), inside the repo locally.
  ```bash
   k scale --env {environment} --cell {cell} --replicas {replica number}
  ```

## Verify

Confirm p95 latency has returned below the alert threshold.

```promql
histogram_quantile(0.95, sum by (le) (rate(matik_http_request_duration_seconds_bucket{service="api", deployment_environment="production"}[5m])))
```

Expected output: value below `1.0` seconds sustained for at least 5 minutes; the alert clears in [#matik-alerts](https://airbnb.enterprise.slack.com/archives/C0BAJU6VCE7).

Confirm in-flight requests are no longer climbing.

```promql
sum(matik_http_active_requests{service="api", deployment_environment="production"})
```

Expected output: a stable, low number of active requests.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Create an incident and Page Matik on-call via PagerDuty, use the [Matik service](https://airbnb.pagerduty.com/service-directory/PM1DKZK) |
| Dependency-owned (MySQL / proxysql `biztech-master.proxysql-production`) | Page the Mysql on-call team via [PagerDuty](https://airbnb.pagerduty.com/service-directory/PSBLRE0) |
| Notify in the Matik internal team channel | Post in [#matik-internal](https://airbnb.enterprise.slack.com/archives/C09GG1KG6ER) |

- Slack: [#matik-internal](https://airbnb.enterprise.slack.com/archives/C09GG1KG6ER) (team), [#matik-alerts](https://airbnb.enterprise.slack.com/archives/C0BAJU6VCE7) (alerts), [#biztech-opseng-goalie](https://airbnb.enterprise.slack.com/archives/C028ENYCAH3) (Ops Eng goalie)
- PagerDuty: [Matik](https://airbnb.pagerduty.com/service-directory/PM1DKZK)

## Rollback

If latency is high after a release, roll back the most recent rollout. For instructions on release check [release process](../release/release-process.md). It will be leveraged via Spinnaker. Please check with dedicated Matik team before performing this step.

## Appendix

### Alert definition

Defined in `api/api_p95_latency_high.ts` (Telescope/CAWS monitor `api_p95_latency_high_production`):

```promql
histogram_quantile(0.95, sum by (le) (rate(matik_http_request_duration_seconds_bucket{service="api", deployment_environment="production"}[5m])))
```

- Recording rule: `matik:api_p95_latency:histogram_p95_rate_5m`
- Metric: `matik_http_request_duration_seconds` (Histogram, `s`). Buckets: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0s (see [Metrics Reference](../observability/metrics.md)).
- Threshold / window: `> 1.0` s, `for: 5m`.
- Severity: critical; notifies `matik-alerts@slack` (#matik-alerts). No PagerDuty target configured.

### Likely causes (most → least likely)

- **Slow database queries / proxysql** — the API's only dependency is `biztech-master.proxysql-production`; slow MySQL directly inflates request latency. Confirm via the by-route p95 query and DB metrics.
- **DB connection-pool saturation** — requests queue waiting for a connection when load exceeds `mysql.api.max_open_conns` / `max_idle_conns` (`_infra/kube/files/matik-api-config.yml`). Confirm via `matik_http_active_requests`.
- **Traffic surge / under-provisioning** — request volume exceeds replica capacity. Confirm via request rate and active requests.
- **Slow downstream on a specific route** — a hot `path` (heavy DAO query or batch endpoint). Confirm via the by-route p95 query.
- **Bad recent deployment** — a regression added latency. Confirm by correlating with the latest Spinnaker rollout.

### Related docs

- [API Guide](../development/api.md)
- [Metrics Reference](../observability/metrics.md)
- [DB Management](../operations/db-management.md)
- Related runbooks: [API Availability Low](api-availability-low.md)

---

Last tested: TODO (set after an Executed or Verified review)
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: TODO
