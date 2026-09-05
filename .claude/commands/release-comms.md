---
description: Draft a Matik release announcement (Slack + GitHub Release body) from the release notes
---

You draft the **release announcement** for a Matik release using the repo comms
template. You produce a **draft only** — you never send or post automatically.

## Inputs (parse from the user's message)

- `version` — the version being announced (e.g. `v0.1.3`). Required; ask if missing.
- `notes` — path to the generated release notes file (default:
  `_infra/docs/release/tmp/<version>.md`), **or** let the user paste them. If
  neither is given, offer to run `/release-notes` first, or read the GitHub
  Release body:
  `GH_HOST=git.musta.ch gh release view <version> --repo airbnb/matik --json body -q .body`.
- `owner` — the release owner to tag for questions. Default: ask, or use the
  triggering user.

## Repo facts

- Template: `_infra/docs/release/templates/release-comms-template.md`.
- Output dir: `_infra/docs/release/tmp/` — if the user asks to save the drafts,
  write them here (e.g. `<version>-comms.md`). This folder is gitignored, so
  drafts are regenerated on demand and never pushed.
- Slack channel: `#biztech-opseng-goalie`.
- Health dashboard: `https://grafana.a.musta.ch/goto/2e1zpMcDR?orgId=1`.
- Release URL: `https://git.musta.ch/airbnb/matik/releases/tag/<version>`.

## Steps

1. **Gather content.** Read/parse the release notes. Extract: a one-line summary,
   the top 3 highlights, and any **action-required** items (migrations, config/
   secret changes, behavior changes). If there is action required, it MUST lead.

2. **Fill the template.** Read
   `_infra/docs/release/templates/release-comms-template.md` and produce both
   variants:
   - **Slack** (primary) — sectioned with emoji headers, suitable for paste into
     `#biztech-opseng-goalie`.
   - **GitHub Release body** — short Markdown header variant.
   Omit the action-required block entirely if there is none.

3. **Output** both drafts in your reply for review.

4. **Offer to send (do not auto-send):** if the user confirms, draft the Slack
   message to `#biztech-opseng-goalie` using the Slack draft tool
   (`slack_send_message_draft`) so the user can review/post it. Never post without
   an explicit go-ahead.

## Refining the template from an example

If the user provides a prior announcement (`example=<path>` or pasted), match its
tone and structure and update
`_infra/docs/release/templates/release-comms-template.md` so future runs follow
the real format.

## Guardrails

- **Never auto-send.** Always stop at a reviewable draft.
- Keep it short and skimmable; link to the full release notes rather than
  reproducing them.
- Lead with action-required items so on-call/downstream teams see them first.
