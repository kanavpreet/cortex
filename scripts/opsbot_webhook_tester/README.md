# OpsBot Webhook Tester

Sends test `incident_channel_summary` OpsBot generic-webhook events to Yoyo's
sandbox callback endpoint, exercising the same path
`chronicler/transformers/matik_webhook.py` validates and routes into Scribe.

## Quick start

1. Open `tester.py` and edit `INCIDENT_REFS` to the incident references to
   post, e.g. `["inc-xxxx", "inc-yyyy"]`.
2. Export the sandbox HMAC secret (never hardcode this — it must not end up
   committed to git):
   ```bash
   export GENERIC_WEBHOOK_SECRET=<sandbox value of chronicler.generic_webhook_secret>
   ```
3. Run it:
   ```bash
   cd scripts
   uv run python -m opsbot_webhook_tester
   ```

One signed POST is sent per entry in `INCIDENT_REFS`, each with a randomly
chosen summary from `sandbox_incident_summaries.json` (made-up OpsBot-style
status/impact/mitigation/root-cause text). The script prints the HTTP status
per incident and exits non-zero if any POST failed.

## Why INCIDENT_REFS is a constant, not a CLI arg

`INCIDENT_REFS` is edited directly in `tester.py` rather than passed as a CLI
argument — it's a Python list, and list literals containing bare identifiers
get mangled by shell globbing (`zsh: no matches found: [inc-xxxx,inc-yyyy]`)
when passed as an argv value. The secret, by contrast, is supplied via the
`GENERIC_WEBHOOK_SECRET` env var rather than a constant, so it's never at risk
of being committed to git.

## Prerequisites

```bash
cd scripts && uv sync   # installs httpx and other deps
```
