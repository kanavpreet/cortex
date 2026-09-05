"""
Service Signature Tester — exercise matik-api's Phase 1c HMAC check.

USAGE
-----
From the scripts/ directory (recommended):
    export MATIK_API_SERVICE_SECRET=<sandbox value of api.service_secret>
    uv run python -m service_signature_tester --base-url https://api-matik-sandbox.a.musta.ch

Or via the top-level dispatcher:
    uv run python -m scripts service-signature-tester

Alongside the usual stdout output, an HTML report (pass/fail table with
status counts) is written next to this script as report.html. Pass
--html-report <path> to write it somewhere else instead.

WHAT IT DOES
------------
Reimplements the HMAC-SHA256 construction from
matik/common/utils/service_auth.py (timestamp + method + path[?query] +
sha256(body), "sha256=" hex digest) so it has no dependency on the `matik`
app package, then fires a fixed matrix of requests at a live matik-api and
prints the actual HTTP status for each:

  - valid_signature        correctly signed request
  - missing_signature      no signature headers at all
  - wrong_secret           signed with a secret that isn't the real one
  - stale_timestamp        correctly signed, but older than the 120s skew
                            window (service_auth.DEFAULT_MAX_SKEW_SECONDS)
  - malformed_timestamp    non-numeric timestamp header
  - tampered_body          signature computed for a different body than
                            the one actually sent
  - tampered_query         signature computed for the bare path, but sent
                            with an appended query param — verifies query
                            strings are covered by the signature too
  - exempt:<path>          /health, /ready, /v1/mcp/health, / — should
                            bypass the check entirely, signed or not

See api/main.py::create_service_signature_dispatch for the server-side
implementation this is testing against.

THE BEFORE/AFTER WORKFLOW
--------------------------
matik-api's check has two independent on/off switches (see
common/models/api_config.py): whether a secret is configured at all, and
enforce_service_signature (shadow vs enforcing). Because shadow mode never
rejects a request, HTTP status alone can't distinguish "not deployed yet"
from "deployed but still in shadow mode" — both look like 200 everywhere.
Run this script at each stage and compare:

  1. BEFORE deploying this change: every case returns 200 (no check exists
     yet). This is your baseline.
  2. AFTER deploying, shadow mode (enforce_service_signature: false,
     today's default): every case should STILL return 200 — shadow mode
     only logs/records metrics, it never rejects. Use --enforced=false
     (the default) and confirm nothing regressed for existing callers.
     Cross-check the matik_api_service_signature_checks_total metric /
     logs (see matik/common/metrics/service_signature_metrics.py) to
     confirm invalid cases are actually being recorded as invalid, not
     silently skipped.
  3. AFTER flipping enforce_service_signature: true for the environment:
     re-run with --enforced. valid_signature and the exempt paths should
     stay 200; every other case should now be 401.

PREREQUISITES
-------------
- The sandbox value of api.service_secret (secret-lair), via the
  MATIK_API_SERVICE_SECRET env var or --secret. Never hardcode it.
- If matik-api isn't reachable without an identity token from where
  you're running this (e.g. a devAccess URL from your laptop rather than
  from inside the cluster), pass --iap-token (or set IAP_TOKEN) with the
  output of `iap-auth https://developers.a.musta.ch`.
"""

import argparse
import hashlib
import hmac
import html
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api-matik-sandbox.a.musta.ch"

DEFAULT_HTML_REPORT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "report.html"
)

SECRET_ENV_VAR = "MATIK_API_SERVICE_SECRET"
IAP_TOKEN_ENV_VAR = "IAP_TOKEN"

_SIGNATURE_PREFIX = "sha256="
TIMESTAMP_HEADER = "X-Matik-Service-Timestamp"
SIGNATURE_HEADER = "X-Matik-Service-Signature"

# Must exceed service_auth.DEFAULT_MAX_SKEW_SECONDS (120s) so the
# stale_timestamp case is rejected on skew, not by chance.
STALE_SKEW_SECONDS = 300

# A real, side-effect-free, no-path-params route (see
# api/routes/incidentio.py::get_last_recorded) used for the GET cases.
GET_TARGET_PATH = "/v1/incidentio/incident/tracker/lastrecorded"
# A real POST route with a body, used for the tampered_body case (see
# api/routes/incidentio.py::get_incident_llm_data).
POST_TARGET_PATH = "/v1/incidentio/incident/hashes"

# Kept in sync with api/main.py::_SIGNATURE_EXEMPT_PATHS.
EXEMPT_PATHS = ["/health", "/ready", "/v1/mcp/health", "/"]


def sign(secret: str, method: str, path: str, body: bytes, timestamp: str) -> str:
    """Mirrors service_auth.sign(): sha256=<hex hmac> over the canonical string."""
    material = b".".join(
        [
            timestamp.encode(),
            method.upper().encode(),
            path.encode(),
            hashlib.sha256(body).hexdigest().encode(),
        ]
    )
    digest = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


@dataclass
class Scenario:
    """One request to fire and check.

    conditional=True means the expected status depends on whether
    enforcement is on (200 shadow / 401 enforced). conditional=False means
    it should always be 200, regardless of enforcement (a valid signature,
    or an exempt path).
    """

    name: str
    method: str
    path: str
    headers: dict[str, str]
    conditional: bool
    body: bytes = b""
    note: str = ""
    # Path actually requested, if different from the signed `path` above
    # (e.g. tampered_query signs for `path` but sends `sent_path`, which has
    # extra/changed query params). Defaults to `path` when unset.
    sent_path: str | None = None


def build_scenarios(secret: str) -> list[Scenario]:
    now = str(int(time.time()))
    stale = str(int(time.time()) - STALE_SKEW_SECONDS)

    tampered_signed_body = b'{"incident_ids": ["SIGNED-FOR-THIS"]}'
    tampered_sent_body = b'{"incident_ids": ["ACTUALLY-SENT-THIS"]}'

    scenarios = [
        Scenario(
            name="valid_signature",
            method="GET",
            path=GET_TARGET_PATH,
            headers={
                TIMESTAMP_HEADER: now,
                SIGNATURE_HEADER: sign(secret, "GET", GET_TARGET_PATH, b"", now),
            },
            conditional=False,
            note="always expect 200",
        ),
        Scenario(
            name="missing_signature",
            method="GET",
            path=GET_TARGET_PATH,
            headers={},
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
        Scenario(
            name="wrong_secret",
            method="GET",
            path=GET_TARGET_PATH,
            headers={
                TIMESTAMP_HEADER: now,
                SIGNATURE_HEADER: sign(
                    "definitely-not-the-real-secret", "GET", GET_TARGET_PATH, b"", now
                ),
            },
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
        Scenario(
            name="stale_timestamp",
            method="GET",
            path=GET_TARGET_PATH,
            headers={
                TIMESTAMP_HEADER: stale,
                SIGNATURE_HEADER: sign(secret, "GET", GET_TARGET_PATH, b"", stale),
            },
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
        Scenario(
            name="malformed_timestamp",
            method="GET",
            path=GET_TARGET_PATH,
            headers={
                TIMESTAMP_HEADER: "not-a-number",
                SIGNATURE_HEADER: "sha256=deadbeef",
            },
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
        Scenario(
            name="tampered_body",
            method="POST",
            path=POST_TARGET_PATH,
            body=tampered_sent_body,
            headers={
                "Content-Type": "application/json",
                TIMESTAMP_HEADER: now,
                SIGNATURE_HEADER: sign(
                    secret, "POST", POST_TARGET_PATH, tampered_signed_body, now
                ),
            },
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
        Scenario(
            # Signed for the bare path (no query), but actually sent with an
            # appended query param — verifies the signature covers the query
            # string, not just path+body (see common/utils/service_auth.py).
            name="tampered_query",
            method="GET",
            path=GET_TARGET_PATH,
            sent_path=f"{GET_TARGET_PATH}?evil=1",
            headers={
                TIMESTAMP_HEADER: now,
                SIGNATURE_HEADER: sign(secret, "GET", GET_TARGET_PATH, b"", now),
            },
            conditional=True,
            note="200 shadow / 401 enforced",
        ),
    ]
    for path in EXEMPT_PATHS:
        scenarios.append(
            Scenario(
                name=f"exempt:{path}",
                method="GET",
                path=path,
                headers={},
                conditional=False,
                note="always expect 200 (signature-exempt route)",
            )
        )
    return scenarios


def run_scenario(
    client: httpx.Client, scenario: Scenario, enforced: bool
) -> tuple[int, bool]:
    """Fire one scenario, return (actual_status, matched_expectation)."""
    response = client.request(
        scenario.method,
        scenario.sent_path or scenario.path,
        content=scenario.body or None,
        headers=scenario.headers,
    )
    expected = 200 if not scenario.conditional else (401 if enforced else 200)
    return response.status_code, response.status_code == expected


_STATUS_STYLE = {
    "ok": ("OK", "#0ca30c", "✓"),
    "mismatch": ("MISMATCH", "#d03b3b", "✗"),
    "error": ("ERROR", "#ec835a", "⚠"),
}


def generate_html_report(
    results: list[dict[str, Any]],
    base_url: str,
    posture: str,
    mismatches: int,
) -> str:
    """Render the scenario results as a self-contained, dark-mode-aware HTML page."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = len(results)
    ok_count = sum(1 for r in results if r["outcome"] == "ok")
    error_count = sum(1 for r in results if r["outcome"] == "error")

    def esc(value: object) -> str:
        return html.escape(str(value))

    rows = []
    for r in results:
        label, color, icon = _STATUS_STYLE[r["outcome"]]
        actual = esc(r["actual"]) if r["actual"] is not None else "—"
        rows.append(
            f"""      <tr>
        <td><span class="badge" style="--badge-color:{color}">{icon} {label}</span></td>
        <td>{esc(r["name"])}</td>
        <td>{esc(r["method"])}</td>
        <td class="path">{esc(r["path"])}</td>
        <td class="num">{actual}</td>
        <td class="num">{esc(r["expected"])}</td>
        <td class="note">{esc(r["note"])}</td>
      </tr>"""
        )

    overall_color = "#0ca30c" if mismatches == 0 else "#d03b3b"
    overall_text = (
        f"All {total} scenarios matched the expected outcome for posture={posture}."
        if mismatches == 0
        else f"{mismatches} of {total} scenario(s) did not match the expected outcome for posture={posture}."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Service Signature Tester Report</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page-plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --border: rgba(11,11,11,0.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page-plane: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --gridline: #2c2c2a;
      --border: rgba(255,255,255,0.10);
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page-plane);
    color: var(--text-primary);
  }}
  .viz-root {{ padding: 32px 24px; max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .meta {{ color: var(--text-secondary); font-size: 13px; margin: 0 0 24px; }}
  .meta span {{ color: var(--text-muted); }}
  .tiles {{ display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }}
  .tile {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
    min-width: 120px;
  }}
  .tile .value {{ font-size: 28px; font-weight: 600; line-height: 1.1; }}
  .tile .label {{ font-size: 12px; color: var(--text-secondary); margin-top: 2px; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    font-size: 13px;
  }}
  th, td {{
    text-align: left;
    padding: 9px 12px;
    border-bottom: 1px solid var(--gridline);
    vertical-align: top;
  }}
  th {{
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }}
  tr:last-child td {{ border-bottom: none; }}
  td.path {{ font-family: ui-monospace, Menlo, monospace; color: var(--text-secondary); }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  td.note {{ color: var(--text-secondary); }}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-weight: 600;
    font-size: 12px;
    color: var(--badge-color);
    white-space: nowrap;
  }}
  .summary {{
    margin-top: 20px;
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface-1);
    color: {overall_color};
    font-weight: 600;
    font-size: 13px;
  }}
</style>
</head>
<body>
  <div class="viz-root">
    <h1>Service Signature Tester Report</h1>
    <p class="meta">
      Target: <span>{esc(base_url)}</span> &middot;
      Posture under test: <span>{esc(posture)}</span> &middot;
      Generated: <span>{esc(generated_at)}</span>
    </p>
    <div class="tiles">
      <div class="tile"><div class="value">{total}</div><div class="label">Scenarios</div></div>
      <div class="tile"><div class="value" style="color:#0ca30c">{ok_count}</div><div class="label">OK</div></div>
      <div class="tile"><div class="value" style="color:#d03b3b">{mismatches}</div><div class="label">Mismatch</div></div>
      <div class="tile"><div class="value" style="color:#ec835a">{error_count}</div><div class="label">Error</div></div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Result</th>
          <th>Scenario</th>
          <th>Method</th>
          <th>Path</th>
          <th>Actual</th>
          <th>Expected</th>
          <th>Note</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(rows)}
      </tbody>
    </table>
    <div class="summary">{esc(overall_text)}</div>
  </div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise matik-api's Phase 1c service-signature check.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"matik-api base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get(SECRET_ENV_VAR),
        help=f"Shared service secret (default: {SECRET_ENV_VAR} env var)",
    )
    parser.add_argument(
        "--iap-token",
        default=os.environ.get(IAP_TOKEN_ENV_VAR),
        help=(
            f"IAP identity token for devAccess URLs, if needed "
            f"(default: {IAP_TOKEN_ENV_VAR} env var). Get one with "
            "`iap-auth https://developers.a.musta.ch`."
        ),
    )
    parser.add_argument(
        "--enforced",
        action="store_true",
        help=(
            "Assert the enforcing posture (bad signatures expected to get "
            "401). Omit for the shadow-mode posture (default): every case "
            "is expected to return 200."
        ),
    )
    parser.add_argument(
        "--html-report",
        metavar="PATH",
        default=DEFAULT_HTML_REPORT_PATH,
        help=(
            "Write an HTML report of the results to PATH, in addition to "
            f"stdout (default: {DEFAULT_HTML_REPORT_PATH}). Pass an empty "
            "string to skip writing the report."
        ),
    )
    args = parser.parse_args()

    if not args.secret:
        print(
            f"Set {SECRET_ENV_VAR} or pass --secret to the sandbox value of "
            "api.service_secret before running.",
            file=sys.stderr,
        )
        return 1

    headers = {}
    if args.iap_token:
        headers["Authorization"] = f"Bearer {args.iap_token}"

    scenarios = build_scenarios(args.secret)
    posture = "enforced" if args.enforced else "shadow"
    print(f"Target: {args.base_url}  Posture under test: {posture}\n")

    mismatches = 0
    results = []
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30.0) as client:
        for scenario in scenarios:
            expected = (
                200 if not scenario.conditional else (401 if args.enforced else 200)
            )
            try:
                status, matched = run_scenario(client, scenario, args.enforced)
            except httpx.HTTPError as e:
                mismatches += 1
                print(f"[ERROR] {scenario.name:<20} {type(e).__name__}: {e}")
                results.append(
                    {
                        "name": scenario.name,
                        "method": scenario.method,
                        "path": scenario.path,
                        "actual": None,
                        "expected": expected,
                        "note": f"{type(e).__name__}: {e}",
                        "outcome": "error",
                    }
                )
                continue

            tag = "ok" if matched else "MISMATCH"
            if not matched:
                mismatches += 1
            print(
                f"[{tag:<8}] {scenario.name:<20} {scenario.method:<4} "
                f"{scenario.path:<45} -> {status}  ({scenario.note})"
            )
            results.append(
                {
                    "name": scenario.name,
                    "method": scenario.method,
                    "path": scenario.path,
                    "actual": status,
                    "expected": expected,
                    "note": scenario.note,
                    "outcome": "ok" if matched else "mismatch",
                }
            )

    print()
    if mismatches:
        print(
            f"{mismatches} scenario(s) did not match the expected outcome for "
            f"posture={posture}. If this is unexpected, check shadow-mode logs "
            "(matik_api_service_signature_checks_total) before assuming a bug."
        )
    else:
        print(f"All scenarios matched the expected outcome for posture={posture}.")

    if args.html_report:
        report_html = generate_html_report(results, args.base_url, posture, mismatches)
        with open(args.html_report, "w") as f:
            f.write(report_html)
        print(f"\nHTML report written to {args.html_report}")

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
