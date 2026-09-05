# Release Comms & Notes

This page covers the two artifacts that go out with every release:

1. **Release notes** — the polished, component-grouped changelog attached to the
   GitHub Release. Generated from GitHub's auto-notes with the
   [`/release-notes`](#release-notes-command) command.
2. **Release comms** — the announcement posted to Slack (and optionally used as
   the GitHub Release body / email). Generated with the
   [`/release-comms`](#release-comms-command) command.

Both commands read a **template stored in this repo** so output stays consistent:

| Artifact | Template |
|----------|----------|
| Release notes | [`templates/release-notes-template.md`](templates/release-notes-template.md) |
| Release comms | [`templates/release-comms-template.md`](templates/release-comms-template.md) |

---

## Why generate, not hand-write

GitHub can auto-generate a raw list of merged PRs between two tags, but that list
is flat, unordered, and full of noise (`[Bulk update]`, dependency bumps, etc.).
The commands turn that raw list into something a reader can scan: grouped **by
component**, with highlights surfaced and — critically — **migrations and
action-required items called out** so on-call and downstream teams aren't
surprised.

---

## `/release-notes` command

**Source:** [`.claude/commands/release-notes.md`](https://git.musta.ch/airbnb/matik/blob/main/.claude/commands/release-notes.md)

**What it does:**

1. Determines the range: `from` = the latest release tag (e.g. `v0.1.2`),
   `to` = `main`/`HEAD` (both overridable).
2. Pulls the raw changes via `gh` (`generate-notes` API + `git log`).
3. Loads the release-notes template and **groups PRs by component** — api, mcp,
   chronicler, scribe, enricher, enigmatologist, historian, migrator, infra/docs.
4. **Flags migrations** when any `matik/migrator/alembic/versions/*` file changed.
5. Emits finished notes ready to paste into the GitHub Release.

**Optional — refine the template from a real example:** pass a path to a previous
release-notes document and the command will match its structure and voice, and
update [`templates/release-notes-template.md`](templates/release-notes-template.md)
accordingly.

**Typical use:**

```text
/release-notes from=v0.1.2 to=main
/release-notes from=v0.1.2 example=path/to/previous-notes.md   # derive/refine the template
```

---

## `/release-comms` command

**Source:** [`.claude/commands/release-comms.md`](https://git.musta.ch/airbnb/matik/blob/main/.claude/commands/release-comms.md)

**What it does:**

1. Takes the finished release notes (or the release tag) as input.
2. Loads the comms template and produces:
   - a **Slack-ready** announcement for
     [#biztech-observability](https://airbnb.enterprise.slack.com/archives/C0207RMSJAC)
     (sectioned, emoji headers, top highlights, action-required callout, links);
   - a **GitHub Release body** variant.
3. **Stops at a draft** — it never auto-sends. Review, then post (or let it draft
   via the Slack tooling).

**Typical use:**

```text
/release-comms version=v0.1.3
/release-comms version=v0.1.3 notes=path/to/generated-notes.md
```

---

## Where comms go

| Channel | When | Format |
|---------|------|--------|
| Slack [#biztech-observability](https://airbnb.enterprise.slack.com/archives/C0207RMSJAC) | Every release | Slack message (primary) |
| GitHub Release body | Every release | Markdown (the release notes themselves) |
| Email | Larger / breaking releases | Formal variant (optional) |

Always lead with **action-required** items (migrations, config/secret changes,
behavior changes) so readers see them first.
