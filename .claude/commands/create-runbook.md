---
description: Generate a boilerplate incident-response runbook for a Matik alert, grounded in repo knowledge
argument-hint: <alert name or symptom> (paste the alert definition / query too)
---

You generate a **per-alert / per-symptom incident-response runbook** for a Matik
service and write it to `_infra/docs/runbooks/`. Runbooks are written for the
on-call engineer staring at a firing alert: **symptom → triage → diagnose →
mitigate → verify → escalate.** They are grounded in what actually exists in this
repo plus the alert definition the user supplies.

## Runbook standard (source of truth for structure)

Every runbook you produce **must conform to the org runbook standard** at:

<https://github.airbnb.biz/Airbnb-ITX/ops_documentation_standards/blob/main/runbook-standard.md>

This command produces the standard's **Type 1: Incident Response** runbook. Follow
the standard's rules without exception:

- **One action per step** — never combine operations into a single numbered step.
- **Imperative mood** — "Restart the deployment", not "You should restart…".
- **Copy-pasteable commands** — no placeholders that require editing. If a value
  varies (namespace, pod name), give the lookup command first, then the command
  that uses a concrete value.
- **No prose inside steps** — move explanations to the Impact/Diagnosis sections.
- Include the **metadata footer** (`Last tested`, `Owner`, `Collaborators`) and
  the required sections defined below.

## Hard requirement: the alert definition

Matik alerts are **not** defined in this repo — they live in Telescope/CAWS
(external). You therefore **cannot** write an accurate runbook from the repo
alone.

**Before doing anything else, confirm the user has provided the alert
definition / alert code.** This means at least one of:

- the alert/monitor query (PromQL, the metric + threshold + window), or
- the alert YAML/config (Telescope custom alert, CAWS monitor, SLO), or
- the exact alert name **plus** its trigger condition and severity.

If the user gave only a vague symptom ("scribe is slow") with no query, threshold,
or alert config, **stop and ask for the alert definition.** Do not invent a
threshold or a metric name. Quote back what you still need. The more context the
user provides (severity, what pages, dashboard links, recent incident), the better
the runbook — ask for it.

`$ARGUMENTS` is the alert name / symptom and may include the pasted definition.

## Repo facts (sources of truth — read these, don't guess)

Matik is a multi-service Python AIOps platform. Ground every claim in these files:

- **Services & topology** — `_infra/mesh.yml` is the authoritative list of
  deployable services, their dependencies, and namespaces. The services are:
  `historian` (variants: `incidentio`, `jira`, `biztech-github`), `chronicler`
  (Kafka consumer), `enricher`, `enigmatologist`, `scribe` (`high`/`medium`/`low`
  priority deployments), `api` (FastAPI), `mcp` (MCP server), `migrator`,
  `sqspurger`, `dbmanager`. Each runs in `matik-sandbox`, `matik-staging`,
  `matik-production`, and (most) `matik-canary` namespaces, mesh `ea1.us`.
- **Metrics** — `_infra/docs/observability/metrics.md` is the full catalog of OTel
  metrics, their types, units, and **labels** (notably `service`, `status`,
  `operation`, `table`, `client`, `endpoint`). Build dashboard/PromQL suggestions
  only from metrics that exist here. Most metrics are prefixed `matik_`.
- **Existing operational knowledge** — reuse and link, don't duplicate:
  - `_infra/docs/operations/historian-failure-handling.md` (tracker tables,
    ERROR-state recovery, the `UPDATE ... tracker SET status='OK'` pattern)
  - `_infra/docs/operations/db-management.md` (admin/dev DB access, MySQL)
  - `_infra/docs/architecture/*.md` (per-service design: chronicler, scribe,
    enricher, historian crawlers, mcp-server, enigmatologist)
  - `_infra/docs/development/*.md` (configuration, dao, logging, metrics)
- **Service config** — `_infra/kube/files/matik-<service>-config.yml` for env vars,
  queues, batch sizes, and tunables a responder may need to change.
- **Source code** — `matik/<service>/` for the actual failure modes, retry logic,
  tracker/state-machine behavior, and queue/DLQ handling.
- **Team / escalation** — `_infra/project.yml` and `_infra/docs/index.md`: team
  Slack is **#biztech-opseng-goalie**, team email `biztech-ops-eng.team@airbnb.com`,
  criticality tier2. Matik also has its own Slack channels: **#matik-internal**
  (team discussion / coordination) and **#matik-alerts** (where alerts fire). Use
  these as escalation defaults unless the user says otherwise.

## Process

1. **Validate the alert input.** If the alert definition is missing (see above),
   ask for it and stop.

2. **Identify the owning service(s).** From the alert's metric labels (e.g.
   `service="scribe"`) and query, map to the service(s) in `mesh.yml`. Read that
   service's architecture doc, config, and source to understand its failure modes.

3. **Mine the repo for grounded content:**
   - Which metric(s) the alert watches and what they mean (`metrics.md`).
   - The realistic failure causes for this service (architecture doc + source).
   - Existing recovery procedures to link/reuse (operations docs — e.g. tracker
     reset for historian, DLQ behavior for scribe/chronicler).
   - Tunables and env vars from the kube config.
   - Upstream/downstream dependencies from `mesh.yml` (e.g. `llm-fusion-hub`,
     `proxysql`/MySQL, Kafka, `matik-api`) — these are blast-radius and
     suspect-cause candidates.

4. **Ask clarifying infra questions when the repo can't answer them** — e.g. which
   dashboard backs this alert, who is paged vs. notified, whether there is a known
   mitigation/feature flag, severity, or SLO. Don't block on nice-to-haves; ask
   only what materially changes the runbook, then proceed with sensible defaults
   and clearly mark assumptions as `> TODO:`.

5. **Write the runbook** using the template below, conforming to the
   [runbook standard](https://github.airbnb.biz/Airbnb-ITX/ops_documentation_standards/blob/main/runbook-standard.md)
   (Type 1: Incident Response). Keep it concrete and action-oriented: real
   `kubectl`/SQL/PromQL commands with concrete namespaces (`matik-production` etc.),
   one atomic action per step, imperative mood, no prose inside steps. Set the
   metadata footer — leave `Last tested` as a `> TODO:` (the standard only allows a
   date after an Executed/Verified run) and default `Owner` to Ops Eng. Mark
   anything you could not verify from the repo or the user as `> TODO:` — never
   fabricate thresholds, metric names, commands, or owners.

6. **Save** to `_infra/docs/runbooks/<service>-<symptom-slug>.md` (kebab-case, e.g.
   `scribe-dlq-growth.md`, `historian-incidentio-error-state.md`). Create the
   `_infra/docs/runbooks/` directory if it doesn't exist.

7. **Register in nav.** Add the runbook to `_infra/portal.yml`. If a top-level
   `Runbooks:` section doesn't exist yet, create one (place it right after
   `Operations:`); otherwise append under it. Use a clear human title, e.g.
   `- Scribe DLQ Growth: runbooks/scribe-dlq-growth.md`.

8. **Report** the file path, the nav entry added, and a bulleted list of every
   `> TODO:` left for a human to fill in.

## Runbook template

This is the **Type 1: Incident Response** template from the
[runbook standard](https://github.airbnb.biz/Airbnb-ITX/ops_documentation_standards/blob/main/runbook-standard.md),
specialized for Matik. Fill every section. Keep steps atomic and imperative; put
all explanation in Impact / Diagnosis, never inside a numbered step. Omit a section
only if it is genuinely N/A, and say why.

````markdown
# [Alert / Symptom Name]

**Type**: Incident Response

| | |
|---|---|
| **Service(s)** | `matik-[service]` (namespaces: sandbox / staging / production) |
| **Severity** | [SEV/priority — from alert definition] |
| **Alert source** | [Telescope / CAWS / SLO — link if provided] — fires in **#matik-alerts** |
| **Owning team** | Ops Eng — #biztech-opseng-goalie (Matik: #matik-internal) |
| **Related docs** | [links to architecture / operations docs] |

## When to Use

- Alert: `[alert_name]`
- Symptoms: [observable behavior that triggers this runbook]

## Alert definition

The exact condition that triggers this alert (as provided by the user):

```promql
[PromQL / monitor query / alert config]
```

- **Metric(s):** `matik_...` — [what it measures, from metrics.md]
- **Threshold / window:** [value over duration]
- **What pages vs. notifies:** [if known]

## Impact

What's degraded when this fires, and the user-facing or data-pipeline impact in
plain language. Upstream/downstream dependencies affected (from mesh.yml): e.g.
data freshness, downstream consumers, dependent services. Helps the responder
prioritize.

## Prerequisites

- [ ] `kubectl` access to the `matik-production` namespace
- [ ] DB access if recovery touches trackers — see [db-management](../operations/db-management.md)
- [ ] [other tools/access this runbook needs]

## Diagnosis — likely causes

Ordered most→least likely, each grounded in how the service actually works:

- **Cause A** — how to confirm (metric/log/query), what it looks like.
- **Cause B** — ...
- **Dependency failure** — [e.g. llm-fusion-hub / MySQL via proxysql / Kafka /
  matik-api unreachable]; how to confirm.

## Steps

One action per step. Commands must be copy-pasteable — if a value varies, give the
lookup command first, then the concrete command.

1. Confirm the alert is genuine (not a deploy blip / known maintenance).

   ```bash
   kubectl -n matik-production logs -l app=matik-[service] --tail=200
   ```

2. Check the dashboard for the firing metric.

   [dashboard link or PromQL to run]

3. [Imperative action — e.g. restart the deployment].

   ```bash
   kubectl -n matik-production rollout restart deploy/matik-[service]
   ```

4. [Next imperative action — e.g. reset the tracker; redrive the DLQ]. See
   [historian-failure-handling](../operations/historian-failure-handling.md) for the
   tracker-reset pattern, or the service config in
   `_infra/kube/files/matik-[service]-config.yml` for queue/DLQ tunables.

## Verify

How to confirm the issue is resolved: the metric returns below threshold, queue
drains, tracker clears, logs clean.

```bash
[exact query/command to confirm resolution]
```

Expected output: [what success looks like]

## Escalate

| Condition | Action |
|---|---|
| Not resolved in [N] minutes | Page Matik on-call via [PagerDuty service — TODO if unknown] |
| Dependency-owned (llm-fusion-hub, Kafka, MySQL) | Page that team |
| Root cause unclear after triage | Post in **#matik-internal** |

- Slack: **#matik-internal** (team), **#matik-alerts** (alerts), **#biztech-opseng-goalie** (Ops Eng goalie)
- PagerDuty: [service name — TODO if unknown]

## Rollback

If the alert started after a release, roll back the bad deploy via Spinnaker.

```bash
[rollback command or Spinnaker pipeline link]
```

## References

- Architecture: [link]
- Metrics: [Metrics Reference](../observability/metrics.md)
- Related runbooks: [links]

---

Last tested: [YYYY-MM-DD] by @[username]
Owner: Ops Eng (#biztech-opseng-goalie)
Collaborators: [list]
````

## Style

- Match the tone of existing `_infra/docs/operations/*.md`: terse, command-first,
  no fluff.
- Use relative markdown links between docs (e.g. `../operations/...`).
- Prefer linking existing docs over restating them.
- Every command must be runnable; every threshold/metric must trace to the alert
  definition or `metrics.md`. If it can't, it's a `> TODO:`.
- Conform to the [runbook standard](https://github.airbnb.biz/Airbnb-ITX/ops_documentation_standards/blob/main/runbook-standard.md):
  atomic steps, imperative mood, copy-pasteable commands, and the metadata footer.
