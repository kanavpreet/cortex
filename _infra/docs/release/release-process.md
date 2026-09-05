# Release Process

This is the operational runbook for shipping a Matik release. It describes the
**how**: each stage, the action you take, and the signal that says you can move
on. For the per-component health checks referenced at every stage, see the
[Validation Checklist](validation-checklist.md).

> **Tooling note:** deployments are driven from **Spinnaker** (pipelines defined
> in [`_infra/cd/pipelines/`](https://git.musta.ch/airbnb/matik/tree/main/_infra/cd/pipelines)).
> Validation is done through **structured logs** and **Grafana** — there is no
> scripted cluster access in this flow.

---

## Environments

| Environment | Namespace | Cells | Notes |
|-------------|-----------|-------|-------|
| sandbox | `matik-sandbox` | `pug` | Single cell. **scribe** runs here (not in staging/prod). Slower connector cadence. |
| staging | `matik-staging` | `pug`, `taz`, `yun` | Multi-cell. Soak target before production. |
| canary | `matik-canary` | `pug`, `taz`, `yun` | Isolated pre-prod slice in the default pipeline. |
| production | `matik-production` | `pug`, `taz`, `yun` | Autoscaled. Aggressive connector cadence. |

Per-environment differences (cron cadence, replicas, LLM models, scribe presence)
are defined in [`_infra/kube/kube-gen.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/kube/kube-gen.yml).

---

## Stage 0 — Pre-release (CI on the PR)

Before anything is releasable it must merge to `main` through a green PR.

- [ ] PR opened; CI passes — `format`, `test`, `lint`, and (when migrations
      changed) `migrations` jobs in [`_infra/ci/jobs/`](https://git.musta.ch/airbnb/matik/tree/main/_infra/ci/jobs).
- [ ] PR reviewed and approved per `CODEOWNERS`.
- [ ] If the PR adds Alembic migrations (`matik/migrator/alembic/versions/*`),
      the description calls out the schema change and whether it is
      backward-compatible.
- [ ] Merged to `main`.

> The `migrations` CI job runs `upgrade head → downgrade base → upgrade head` to
> prove migrations are reversible and idempotent. Treat a failure here as a
> release blocker.

---

## Stage 1 — Sandbox

**Action:** trigger the **`deploy_to_sandbox`** Spinnaker pipeline
([`deploy_to_sandbox.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/cd/pipelines/deploy_to_sandbox.yml)).
It can run from any branch and performs: mesh config → migrations
(`matik-migrator`) → services → historian jobs.

- [ ] Pipeline completed green (mesh, migrations, services, jobs).
- [ ] `matik-migrator` job finished; DB at expected revision (see
      [migrator validation](validation-checklist.md#migrator)).
- [ ] Each service passes its [per-component checks](validation-checklist.md)
      in sandbox (`pug`): no crash loops, expected startup log lines present.
- [ ] Integration tests pass (Pokey — `api` + `smoke` markers), when enabled.
- [ ] No errors in application logs over a short observation window.

> Sandbox is the only environment where **scribe** (high/medium/low) is deployed.
> Validate scribe here.

---

## Stage 2 — Staging

**Action:** trigger the **`deploy_to_staging`** Spinnaker pipeline
([`deploy_to_staging.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/cd/pipelines/deploy_to_staging.yml)).
Deploys across all three cells (`pug`, `taz`, `yun`).

- [ ] Pipeline completed green across all cells.
- [ ] Integration tests pass (Pokey — `api` + `smoke` markers), when enabled.
- [ ] **Soak:** application runs in staging for the agreed minimum soak period
      without restarts or error spikes.
- [ ] [Grafana service-health dashboard](https://grafana.a.musta.ch/goto/ffo8nog9p7ke8d?orgId=1)
      reviewed — no anomalies in latency, error rates, or DB connections.
- [ ] No active incidents or alerts triggered during soak.
- [ ] Every component passes its [staging promotion gate](validation-checklist.md#promotion-gate-matrix).

---

## Stage 3 — Cut the GitHub Release

Once staging is healthy and soaked, create the release tag.

- [ ] Decide the version bump (`vX.Y.Z`) per
      [semantic versioning](versioning.md).
- [ ] Generate release notes with the **[`/release-notes`](release-comms.md)**
      command (turns GitHub's auto-notes into polished, component-grouped notes
      and flags any migrations).
- [ ] Create the GitHub Release with tag `vX.Y.Z` and the generated notes.
- [ ] Notes include: summary, per-component changes, **migration / action-required**
      callouts, and known issues.

---

## Stage 4 — Default pipeline → canary → production

**Action:** the **default** pipeline
([`default.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/cd/pipelines/default.yml))
runs on a schedule (**Mondays 14:00 UTC**) or can be triggered manually from
`main`. It deploys staging → **manual judgment gate** → production (bluegreen,
~5-minute pause per cell). Migrations run as the `matik-migrator` job before each
environment.

- [ ] Confirm the **release tag** is the intended one before approving.
- [ ] Approve the **manual judgment** gate.
- [ ] Production migrations (`matik-migrator`) completed; DB at the release
      revision.
- [ ] Production deploy green across `pug`, `taz`, `yun`.
- [ ] Every component passes its [production promotion gate](validation-checklist.md#promotion-gate-matrix).
- [ ] Post-deploy: [Grafana](https://grafana.a.musta.ch/goto/ffo8nog9p7ke8d?orgId=1)
      shows steady error/latency; historian cronjobs fire and advance their
      trackers; SQS + DLQ depths are sane.
- [ ] Send release comms with the **[`/release-comms`](release-comms.md)** command.

---

## Special paths

### Emergency hotfix

Use [`emergency.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/cd/pipelines/emergency.yml)
**only** for urgent production fixes that cannot wait for the normal soak. It
deploys straight to production and canary with a faster rollout strategy and
checks that the default pipeline is not already running.

- ⚠️ Skips the staging soak — keep the change minimal and well-understood.
- [ ] Still open a PR and pass CI before merging the hotfix to `main`.
- [ ] After the hotfix lands, cut a patch release (`vX.Y.Z+1`) so the tag history
      reflects what is in production.
- [ ] Validate production per the [checklist](validation-checklist.md) immediately
      after rollout.

### Reset sandbox

Use [`reset_sandbox.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/cd/pipelines/reset_sandbox.yml)
to fully reset the sandbox environment (scale services to 0 → downgrade DB to base
→ purge SQS queues → wait → redeploy). See the
[Reset Matik runbook](../development/reset-matik.md) for details.

- ⚠️ Destructive — sandbox data and queue contents are purged. Sandbox only.

---

## Rollback

If a release misbehaves in production:

1. **Re-deploy the previous good release tag** through the default pipeline
   (fastest, safest for app-only regressions).
2. **Schema rollback** (only if a migration is implicated and is reversible):
   run the `matik-migrator-downgrade` job
   ([`matik-migrator-downgrade.yml`](https://git.musta.ch/airbnb/matik/blob/main/_infra/kube/apps/matik-migrator-downgrade.yml)).
   Confirm the migration's `downgrade()` is safe before using this in production.
3. Communicate the rollback in [#biztech-opseng-goalie](https://airbnb.enterprise.slack.com/archives/C028ENYCAH3)
   and note it in the release thread.

> Prefer forward-fixes for data-affecting changes; downgrades that drop columns or
> tables can lose data.

---

## Roles & who to ping

| Concern | Contact |
|---------|---------|
| Product / DRI | Julie Trias |
| Eng Manager | Linh Duong |
| Team channel | [#biztech-opseng-goalie](https://airbnb.enterprise.slack.com/archives/C028ENYCAH3) |
| Component owners | See [`CODEOWNERS`](https://git.musta.ch/airbnb/matik/blob/main/CODEOWNERS) |
