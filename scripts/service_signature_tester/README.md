# Service Signature Tester

Exercises matik-api's Phase 1c service-to-service HMAC signature check
(`api/main.py::create_service_signature_dispatch`, backed by
`common/utils/service_auth.py`) against a live environment, without any
dependency on the `matik` app package.

## Quick start

```bash
export MATIK_API_SERVICE_SECRET=<sandbox value of api.service_secret>
cd scripts
uv run python -m service_signature_tester --base-url https://api-matik-sandbox.a.musta.ch
```

If you're calling a devAccess URL from outside the cluster, also pass an
IAP token:

```bash
export IAP_TOKEN=$(iap-auth https://developers.a.musta.ch)
uv run python -m service_signature_tester --iap-token "$IAP_TOKEN"
```

Fires a fixed matrix of requests — a correctly signed request, a missing
signature, a signature made with the wrong secret, a stale timestamp, a
malformed timestamp, a signature that doesn't match the body actually
sent, and each signature-exempt path — and prints the actual HTTP status
for each.

Every run also writes a self-contained HTML report (status tiles + a
pass/fail table, dark-mode aware) to `report.html` next to `tester.py` —
easier to skim or share than the terminal output. Pass `--html-report
<path>` to write it elsewhere, or `--html-report ""` to skip it:

```bash
uv run python -m service_signature_tester
open report.html
```

## The before/after workflow

The check has two independent switches (`common/models/api_config.py`):
whether a secret is configured, and `enforce_service_signature` (shadow
vs. enforcing). Shadow mode never rejects a request, so HTTP status alone
can't tell "not deployed yet" apart from "deployed but still shadow" —
both look like all-200. Run this at each stage of the rollout:

1. **Before deploying** this change to an environment: every case returns
   200 (no check exists yet). This is your baseline.
2. **After deploying**, shadow mode (`enforce_service_signature: false`,
   today's default): run with no flags. Every case should *still* return
   200 — shadow mode only logs and records
   `matik_api_service_signature_checks_total`. Confirm nothing regressed
   for existing callers, then check that metric/the logs to confirm the
   invalid cases are actually being recorded as invalid.
3. **After flipping `enforce_service_signature: true`** for the
   environment: run with `--enforced`. `valid_signature` and the exempt
   paths should stay 200; every other case should now be 401.

The script exits non-zero if any case didn't match the expected outcome
for the posture (`--enforced` or not) you told it you're testing.

## Prerequisites

```bash
cd scripts && uv sync   # installs httpx and other deps
```

- The sandbox value of `api.service_secret` (secret-lair) — never hardcode
  it, only pass it via `MATIK_API_SERVICE_SECRET` / `--secret`.
- An IAP token if the target URL isn't reachable without one from where
  you're running this.
