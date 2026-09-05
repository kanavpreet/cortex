<!--
Release comms template for Matik.

Used by the /release-comms command. Two variants below:
  1. Slack announcement (primary) — for #biztech-opseng-goalie
  2. GitHub Release body — Markdown for the Releases page

Placeholders in {{double braces}} are filled by the command. This is a SCAFFOLD —
refine it by running `/release-comms` against a real prior announcement to match
the team's voice. Keep it short; link out to the full release notes.
-->

## Variant 1 — Slack announcement (primary)

```
:rocket: *Matik {{version}} is live* :rocket:

{{one_line_summary}}

*Top changes*
• {{highlight_1}}
• {{highlight_2}}
• {{highlight_3}}

{{#if_action_required}}:warning: *Action required* — {{action_required_summary}}{{/if}}

:memo: Release notes: {{release_url}}
:bar_chart: Health dashboard: https://grafana.a.musta.ch/goto/ffo8nog9p7ke8d?orgId=1
:book: Docs: https://git.musta.ch/airbnb/matik

Questions → this thread or ping the release owner ({{release_owner}}).
```

---

## Variant 2 — GitHub Release body

<!-- Usually this is the generated release notes themselves. Use this short header
     when a separate, comms-style body is wanted. -->

**Matik {{version}}** — {{one_line_summary}}

**Highlights**
- {{highlight_1}}
- {{highlight_2}}
- {{highlight_3}}

{{#if_action_required}}> ⚠️ **Action required:** {{action_required_summary}}{{/if}}

Full notes below · [Health dashboard](https://grafana.a.musta.ch/goto/ffo8nog9p7ke8d?orgId=1) · [Docs](https://git.musta.ch/airbnb/matik)

---

_Generated with `/release-comms`. Review before posting — the command never sends automatically._
