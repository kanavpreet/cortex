"""
OpsBot Webhook Tester — POST test incident-channel-summary events to Yoyo sandbox.

USAGE
-----
From the scripts/ directory (recommended):
    uv run python -m opsbot_webhook_tester

Edit INCIDENT_REFS below, and set the GENERIC_WEBHOOK_SECRET env var, before
running.

WHAT IT DOES
------------
For each incident reference in INCIDENT_REFS, builds one
``incident_channel_summary`` OpsBot generic-webhook payload (matching the
contract in ``matik/chronicler/transformers/matik_webhook.py``), signs it with
HMAC-SHA256, and POSTs it to Yoyo's sandbox callback endpoint. The summary text
for each payload is picked at random from ``sandbox_incident_summaries.json``,
a small set of made-up OpsBot-style summaries (status, impact, mitigation,
root cause) meant to exercise what the enrichment/correlation path actually
reads out of a summary.

PREREQUISITES
-------------
- Export GENERIC_WEBHOOK_SECRET to the sandbox value of
  chronicler.generic_webhook_secret (secret-lair) before running:
      export GENERIC_WEBHOOK_SECRET=<sandbox secret>
"""

import hashlib
import hmac
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

YOYO_SANDBOX_URL = "https://callbacks.airbnb.com/v1/external/callback/matik/sandbox/generic/webhook/events"

# Sandbox value of chronicler.generic_webhook_secret (secret-lair), supplied
# via env var rather than hardcoded so it never ends up committed to git.
GENERIC_WEBHOOK_SECRET_ENV_VAR = "GENERIC_WEBHOOK_SECRET"

# Incident references to post one test webhook event for. Edit this list
# before running.
INCIDENT_REFS: list[str] = ["INC-7818", "INC-7741", "INC-7713", "INC-7684"]

SIGNATURE_HEADER = "X-Matik-Signature-256"
SUMMARIES_FILE = Path(__file__).parent / "sandbox_incident_summaries.json"


def load_summaries(path: Path = SUMMARIES_FILE) -> list[str]:
    """Load the made-up OpsBot summary pool from ``path``."""
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 over the raw body, matching the transformer's validation."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_payload(incident_ref: str, summary: str) -> dict[str, Any]:
    """Build the OpsBot incident_channel_summary payload envelope."""
    return {
        "category": "incident_channel_summary",
        "source": "opsbot",
        "entity_id": incident_ref,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {"summary": summary},
    }


def post_summary(
    client: httpx.Client,
    url: str,
    secret: str,
    incident_ref: str,
    summary: str,
) -> httpx.Response:
    """Sign and POST one incident-channel-summary payload."""
    body = json.dumps(build_payload(incident_ref, summary)).encode()
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(body, secret),
    }
    return client.post(url, content=body, headers=headers)


def main() -> int:
    secret = os.environ.get(GENERIC_WEBHOOK_SECRET_ENV_VAR)
    if not secret:
        print(
            f"Set the {GENERIC_WEBHOOK_SECRET_ENV_VAR} env var to the sandbox "
            "chronicler.generic_webhook_secret value before running.",
            file=sys.stderr,
        )
        return 1

    summaries = load_summaries()

    failures = 0
    with httpx.Client(timeout=30.0) as client:
        for incident_ref in INCIDENT_REFS:
            summary = random.choice(summaries)
            try:
                response = post_summary(
                    client, YOYO_SANDBOX_URL, secret, incident_ref, summary
                )
            except httpx.HTTPError as e:
                failures += 1
                print(f"[FAILED] {incident_ref} -> {type(e).__name__}: {e}")
                continue

            status = "ok" if response.is_success else "FAILED"
            print(f"[{status}] {incident_ref} -> {response.status_code}")
            if not response.is_success:
                failures += 1
                print(f"    body: {response.text}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
