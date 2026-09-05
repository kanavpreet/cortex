---
description: Generate polished, component-grouped Matik release notes from GitHub's auto-generated notes
---

You generate **Matik release notes** by turning GitHub's flat auto-generated notes
into the polished, component-grouped format defined by the repo template.

## Prerequisites (one-time)

The `gh` CLI must be authenticated against the GitHub Enterprise host
**`git.musta.ch`** before this command can fetch notes. Check with
`GH_HOST=git.musta.ch gh auth status`. If it is not logged in, run:

```bash
gh auth login -h git.musta.ch
# Protocol: HTTPS · Authenticate Git: Yes · Method: Login with a web browser
# Copy the one-time code, press Enter, complete login in the browser.
# Success looks like: "✓ Logged in as <ldap>"
```

This is a one-time setup per machine. As the first step, verify auth and — if it
fails — print the `gh auth login -h git.musta.ch` instructions and stop, rather
than producing partial notes from `git log` alone (unless the user explicitly asks
for the offline fallback).

## Inputs (parse from the user's message; all optional)

- `from` — the starting tag/ref. Default: the **latest release tag** (e.g. `v0.1.2`).
- `to` — the ending ref. Default: `main` (fall back to `HEAD`).
- `version` — the version being released (e.g. `v0.1.3`). If omitted, propose the
  next semver bump and ask the user to confirm.
- `example` — path to a previous release-notes document. If provided, derive the
  structure/voice from it and update the template (see step 6).

## Repo facts

- GitHub Enterprise host: `git.musta.ch`, repo `airbnb/matik`. Prefix `gh`
  invocations with `GH_HOST=git.musta.ch` and `--repo airbnb/matik`.
- Template: `_infra/docs/release/templates/release-notes-template.md`.
- Output dir: `_infra/docs/release/tmp/` — generated notes are written here. This
  folder is gitignored, so drafts are regenerated on demand and never pushed.
  Create it if missing (`mkdir -p _infra/docs/release/tmp`).
- Migration files live under `matik/migrator/alembic/versions/`.

## Steps

1. **Resolve the range.** If `from` is not given, get the latest tag:
   `git describe --tags --abbrev=0` (or `git tag --sort=-v:refname | head -1`).
   Confirm `to` (default `main`).

2. **Fetch the raw changes.** Use both sources and reconcile:
   - PR list / GitHub notes:
     `GH_HOST=git.musta.ch gh api repos/airbnb/matik/releases/generate-notes -f tag_name=<version> -f previous_tag_name=<from> -f target_commitish=<to>`
     (or `gh release create ... --generate-notes --draft` if the user wants a draft).
   - Commit log for completeness:
     `git log --no-merges --pretty='%s (%h)' <from>..<to>`.

2b. **Resolve real contributors.** Never use the generic "multiple engineers
    across BizTech and partner teams" filler — always attribute by GitHub
    handle. Extract every PR number referenced in the generate-notes output
    (the `pull/NNN` links), then for each one fetch its author's login:
    `GH_HOST=git.musta.ch gh pr view <N> --repo airbnb/matik --json author --jq '.author.login'`.
    Group PR numbers by author (`git log --pretty='%an <%ae>' <from>..<to>` is
    useful as a cross-check but is NOT a substitute — it gives the git commit
    identity, not the GitHub handle, and squash-merges attribute every commit
    in the PR to whoever merged it). Carry this `{login: [PR numbers]}` mapping
    into step 5.

3. **Detect migrations.** Check whether any migration files changed:
   `git diff --name-only <from>..<to> -- matik/migrator/alembic/versions/`.
   If any are present, populate the **Action required → Migrations** line with the
   migration revision(s) and a one-line description, and call it out in the TL;DR.

4. **Group by theme.** The template (derived from the v0.1.0 release notes) groups
   work under themed "Key Highlights" sub-sections, not raw service names. Bucket
   each PR/commit into the section it best fits:
   - **Core Platform** — scaffolding, architecture, shared framework changes
   - **Integrations** — GHE, Jira, Incident.io, PagerDuty connectors
   - **API and Service Layer** — api, mcp, facade, correlation endpoints
   - **Infrastructure and Deployment** — `_infra/`, kube-gen, CI/CD, IAM, mesh
   - **Observability and Instrumentation** — metrics, logging, tracing (Telescope/OTEL)
   - **Developer Experience** — tooling, linting, tests, local dev, pre-commit
   - **Data Modeling and Processing** — DAOs, migrations, schema, dedup/hashing
   - **Documentation and Tooling** — docs, templates, CODEOWNERS, PR templates

   Use PR title prefixes (e.g. `[scribe]`, `fix:`) and changed paths
   (`matik/<service>/...`, `_infra/...`) to classify. Drop pure noise (dependency
   bumps, `[Bulk update]`) or collapse it into a single line.

5. **Fill the template.** Read
   `_infra/docs/release/templates/release-notes-template.md` and follow its prose
   voice: a short narrative intro (1–2 paragraphs on what the release marks), then
   the themed sub-sections separated by the `⸻` glyph, ending with a Contributors
   note. Fill the `Matik {{version}} — {{release_theme}}` title with a concise
   theme. **Delete any sub-section with no content.** Keep bullets reader-facing
   (what changed + why it matters), not raw commit subjects. Reference PRs as
   `(#NNN)` where useful. Include the **Upgrade Notes** block only when step 3
   found migrations or other action-required changes.

   For **Contributors**, use the `{login: [PR numbers]}` mapping from step 2b:
   `This release was authored by @<login> (#N, #N) and @<login> (#N).` — one
   clause per contributor, comma-separated, PR numbers in the order they appear
   in the range. A single-author release still names them explicitly ("authored
   entirely by @<login>"). Never fall back to the generic multi-engineer
   boilerplate — if `gh` can't resolve an author for some reason, say so rather
   than papering over it with the filler sentence.

6. **If `example` was provided:** read it, match its section order, headings, and
   tone, and overwrite `_infra/docs/release/templates/release-notes-template.md`
   so future runs follow the real format. Then produce the notes in that shape.

7. **Output** the finished release notes in your reply, and save them to
   `_infra/docs/release/tmp/<version>.md` (the gitignored output dir; create it
   with `mkdir -p` if missing). Offer to attach them to a GitHub Release draft
   (`gh release create <version> --notes-file _infra/docs/release/tmp/<version>.md --draft`)
   only if the user asks — **do not create or publish a release without explicit
   confirmation.**

## Guardrails

- Never push tags or publish releases without the user explicitly confirming.
- If `gh` is not authenticated against `git.musta.ch`, say so and fall back to
  `git log` only, noting that PR numbers/links may be incomplete.
- Keep notes concise; the full PR list is one `compare` link away
  (`https://git.musta.ch/airbnb/matik/compare/<from>...<to>`).
