# Matik API Availability Low

**Type**: Incident Response

| | |
|---|---|
| **Last Reviewed** | 2026-07-21 |
| **Reviewed By** | @alfredo_moreira |


## When to Use

- Alerts: `Alert-matikApiAvailability-fast-burn` (critical), `Alert-matikApiAvailability-slow-burn` (warning), `Alert-matikApiAvailability-glacial-burn` (warning)
- Symptoms: Production API 5xx errors are burning the 4-week availability error budget fast enough that the SLO is on track to breach within days.

This is now a **multi-window, multi-burn-rate SLO alert**, not a fixed threshold. Airbnb measures the API availability SLO (99.5% target) over a rolling 28-day window, so a short dip below 99.5% does not by itself breach the SLO — it means the error budget (0.5% of the 28-day window, i.e. 672h) is being burned faster than the window allows. The alert fires when the *burn rate* implies the budget will run out soon:

| Alert | Long window | Burn rate threshold | Severity | Budget exhausted in |
| --- | --- | --- | --- | --- |
| `Alert-matikApiAvailability-fast-burn` | 1h | 14.4x | critical | ~47h (~2 days) |
| `Alert-matikApiAvailability-slow-burn` | 6h | 6x | warning | ~112h (~4.7 days) |
| `Alert-matikApiAvailability-glacial-burn` | 24h | 3x | warning | ~224h (~9.3 days) |

## Impact

The API is failing enough requests that, at the current rate, it is on track to burn through its entire 4-week (99.5%) availability error budget within days. This replaces the previous fixed threshold (`availability < 99.5% for 5m`), which was wrong on two counts: a 5-minute dip does not breach a 28-day SLO, and sitting at exactly 99.5% would still exhaust the budget only at the end of the full window — the old alert fired on noise, not genuine risk to the SLO. A 5xx spike or a drop in successful traffic both pull availability down and drive the burn rate — the standalone `API 5xx Rate High` alert has since been removed, so this availability/burn-rate alert is now the primary signal for elevated 5xx errors. Because every Matik service (historians, enigmatologist, enricher, mcp, integration tests) depends on `matik-api-production` per `_infra/mesh.yml`, sustained burn degrades data freshness and the enrichment/correlation pipeline platform-wide. See the [Appendix](#appendix) for likely causes.

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

1. Inspect the API logs for errors. This can be done:

- Via [Grafana Watchpoint](https://grafana.a.musta.ch/a/watchpoint?wp=workspace[0]:logs/watchpoint-logs?container=matik-api&from=now-6h&kube_role=matik-api-production&to=now&var-project=matik)
- Via [Headlamp](https://headlamp.a.musta.ch/projects/matik/production/matik-api?cellset=prod-use1). There is an option to stream logs, by selecting a pod and then clicking the stream logs button.
- Via k tools by running the following commands in the project repo:

   ```bash
   k logs
   ```
And follow the interactive terminal prompts.

2. Check whether availability is dropping due to 5xx (the companion metric). Use the `metrics/matik` tenant.

   ```promql
   sum(rate(matik_http_requests_total{service="api", status=~"5..", deployment_environment="production"}[5m]))
   ```

3. Break the errors down by route and status to localize the failure.

   ```promql
   sum by (path, status) (rate(matik_http_requests_total{service="api", status=~"5..", deployment_environment="production"}[5m]))
   ```

4. Check the database readiness probe to rule out a DB outage.

   ```bash
   IAP_TOKEN="$(iap-auth https://api-matik.a.musta.ch)"
   curl -H "Proxy-Authorization: Bearer $IAP_TOKEN" https://api-matik.a.musta.ch/ready
   ```

   Expected output: `{"status": "ready", ... "checks": {"database": "ok"}}`.

5. Check pod health and replica count for crash loops or unavailable pods, via [Headlamp](https://headlamp.a.musta.ch/projects/matik/production/matik-api?cellset=prod-use1)



6. List recent deployments to check whether the dip correlates with a release.
   1. Check the Spinnaker **default** [pipeline job](https://spinnaker.a.musta.ch/#/applications/matik/executions/01KY09E04JXN1VWVEGHW34XD52?stage=1&step=1&details=Job%20Status).
   2. Check [Git releases](https://git.musta.ch/airbnb/matik/releases)


7. If a recent deploy is implicated, roll it back (see [Rollback](#rollback)).

8. If pods are unhealthy and no deploy is implicated, restart the deployment, by using the [k tool](https://developers.a.musta.ch/docs/default/component/kube-gen/getting-started/k-tools/#k-cycle-restart-all-of-the-pods-in-your-deployment) in the Matik repo locally.

   ```bash
   k cycle
   ```

## Verify

Confirm the burn rate has dropped back below the fast-burn threshold (14.4x over 1h).

```promql
1 - (
  sum(rate(matik_http_requests_total{service="api", status!~"5..", deployment_environment="production"}[1h]))
  /
  sum(rate(matik_http_requests_total{service="api", deployment_environment="production"}[1h]))
)
```

Expected output: value below `0.005 * 14.4` (`0.072`) sustained for the long window; the alerts clear in [#matik-alerts](https://airbnb.enterprise.slack.com/archives/C0BAJU6VCE7). Also check the current instantaneous ratio as a quick diagnostic signal:

```promql
sum(rate(matik_http_requests_total{service="api", status!~"5..", deployment_environment="production"}[5m])) / sum(rate(matik_http_requests_total{service="api", deployment_environment="production"}[5m]))
```

Expected output: value at or above `0.995`.

Confirm the readiness probe is healthy.

```bash
  IAP_TOKEN="$(iap-auth https://api-matik.a.musta.ch)"
  curl -H "Proxy-Authorization: Bearer $IAP_TOKEN" https://api-matik.a.musta.ch/ready
```

Expected output: `{"status": "ready", ... "checks": {"database": "ok"}}`.

## Escalate

| Condition | Action |
| --- | --- |
| Not resolved in 30 minutes | Create an incident and Page Matik on-call via PagerDuty, use the [Matik service](https://airbnb.pagerduty.com/service-directory/PM1DKZK) |
| Dependency-owned (MySQL / proxysql `biztech-master.proxysql-production`) | Page the Mysql on-call team via [PagerDuty](https://airbnb.pagerduty.com/service-directory/PSBLRE0) |
| Notify in the Matik internal team channel | Post in [#matik-internal](https://airbnb.enterprise.slack.com/archives/C09GG1KG6ER) |

- Slack: [#matik-internal](https://airbnb.enterprise.slack.com/archives/C09GG1KG6ER) (team), [#matik-alerts](https://airbnb.enterprise.slack.com/archives/C0BAJU6VCE7) (alerts), [#biztech-opseng-goalie](https://airbnb.enterprise.slack.com/archives/C028ENYCAH3) (Ops Eng goalie)
- PagerDuty: [Matik](https://airbnb.pagerduty.com/service-directory/PM1DKZK)

## Rollback

If availability dropped after a release, roll back the most recent rollout. For instructions on release check [release process](../release/release-process.md). It will be leveraged via Spinnaker. Please check with dedicated Matik team before performing this step.



## Appendix

### Alert definition

Defined in `api/api_availability_low.ts` as a `RatioSLO` (id `matikApiAvailability`, title "[matik_api] API availability") with three `BurnRateAlert`s (Telescope/CAWS monitors `Alert-matikApiAvailability-fast-burn`, `Alert-matikApiAvailability-slow-burn`, `Alert-matikApiAvailability-glacial-burn`):

- **SLO target:** 99.5% over a rolling `28d` window (error budget = 0.5% of 672h).
- **Good events:** `matik_http_requests_total{service="api", deployment_environment="production", status!~"5.."}` (rate, summed).
- **Total events:** `matik_http_requests_total{service="api", deployment_environment="production"}` (rate, summed).
- **Burn-rate thresholds:**

  | Alert id | Long window | Burn rate | Severity |
  | --- | --- | --- | --- |
  | `Alert-matikApiAvailability-fast-burn` | 1h | `> 14.4x` | critical |
  | `Alert-matikApiAvailability-slow-burn` | 6h | `> 6x` | warning |
  | `Alert-matikApiAvailability-glacial-burn` | 24h | `> 3x` | warning |

- Severity: critical (fast-burn) / warning (slow, glacial); all notify `matik-alerts@slack` (#matik-alerts) with `disableRenotify: true`. No PagerDuty target configured.
- This replaces the prior fixed-threshold alert (`< 0.995` for `5m`), which could fire on a brief dip that posed no real risk to the 28-day SLO.

### Likely causes (most → least likely)

- **5xx error spike** — good events exclude `5..`, so any surge in server errors raises the burn rate. Break down `matik_http_requests_total{status=~"5.."}` by `path` to localize the failure (step 2 below).
- **Database / proxysql unavailable** — the API's only dependency is `biztech-master.proxysql-production`; DB failures become 500s. Confirm via `/ready` (503 when DB is down).
- **Bad recent deployment** — a regression returning errors on a subset of routes. Confirm by correlating with the latest Spinnaker rollout.
- **Pod unavailability / crash loop** — fewer healthy replicas serving traffic. Confirm via pod status and restart counts.

### Related docs

- [API Guide](../development/api.md)
- [Metrics Reference](../observability/metrics.md)
- [DB Management](../operations/db-management.md)
- Related runbooks: [API p95 Latency High](api-p95-latency-high.md)

---

Last tested: 2026-07-21
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: N/A
