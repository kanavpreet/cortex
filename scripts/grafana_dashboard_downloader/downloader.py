"""
Grafana Dashboard Downloader — fetch dashboard JSON models from a Grafana link.

USAGE
-----
From the scripts/ directory (recommended):
    uv run python -m grafana_dashboard_downloader <url> [options]

Or via the top-level dispatcher:
    uv run python -m scripts download-grafana-dashboard <url>

Grafana is IAP-protected, so authentication follows the same pattern as other
local-dev IAP calls in this repo (see matik/scripts/run_eval.sh and
_infra/docs/runbooks/api-availability-low.md): a token from `iap-auth
<base-url>`, sent as `Proxy-Authorization: Bearer <token>`. If `airtool
install iap-auth` is set up, no manual step is needed — the tool fetches a
fresh token itself:
    uv run python -m grafana_dashboard_downloader \
        https://grafana.a.musta.ch/d/matik-chronicler/matik-chronicler-dashboard?orgId=1

To reuse an existing token instead (e.g. IAP tokens are short-lived, ~1h),
set $IAP_TOKEN or pass --token:
    export IAP_TOKEN="$(iap-auth https://grafana.a.musta.ch)"

WHAT IT DOES
------------
The <url> can be either a single dashboard link or a folder link:

- Dashboard link (`/d/<uid>/...`): fetches that one dashboard.
- Folder link (`/dashboards/f/<uid>/...`): lists every dashboard directly
  inside the folder (via the Grafana search API) and fetches each one. This
  is NOT recursive — dashboards inside subfolders are skipped, and their
  titles are printed so you can re-run against each subfolder link if needed.

Short `/goto/<hash>` links are also accepted; they're resolved to their
`/d/` or `/dashboards/f/` target with one redirect hop first.

For each dashboard, GET {base_url}/api/dashboards/uid/{uid} is called and just
the `dashboard` object (not the `meta` wrapper Grafana's API returns it in) is
written to <output-dir>/<uid>.json, pretty-printed with a 2-space indent and
alphabetically sorted keys — the same format Grafana's own "Dashboard
settings -> JSON Model" view produces, matching the dashboard JSON files
already checked in under matik/local-configs/grafana/provisioning/dashboards/.

Default output directory is downloads/ next to this script (i.e. anchored to
this file's own location, not the caller's cwd — the default is a plain
relative path resolved against wherever you happen to run the command from).
Override with --output-dir, e.g. to write straight into
matik/local-configs/grafana/provisioning/dashboards/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from common.console import print_error, print_info, print_success, print_warning

IAP_TOKEN_ENV_VAR = "IAP_TOKEN"
IAP_AUTH_TIMEOUT_SECONDS = 30

# Anchored to this file's own directory (not the caller's cwd) so the default
# doesn't depend on where the command happens to be run from.
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "downloads"

# Grafana search API caps results per page; this is high enough to cover any
# folder this tool is realistically pointed at in one call.
_FOLDER_SEARCH_LIMIT = 5000


def resolve_token(explicit_token: str | None, base_url: str) -> str:
    """
    Resolve the IAP token to authenticate to Grafana with.

    Precedence: --token flag > $IAP_TOKEN env var > a fresh `iap-auth
    <base_url>` call. Raises RuntimeError with a user-actionable message if
    none of those produce a token.
    """
    if explicit_token:
        return explicit_token

    env_token = os.environ.get(IAP_TOKEN_ENV_VAR)
    if env_token:
        return env_token

    return _fetch_iap_token(base_url)


def _fetch_iap_token(base_url: str) -> str:
    """Run `iap-auth <base_url>` and return the token it prints to stdout."""
    try:
        result = subprocess.run(
            ["iap-auth", base_url],
            capture_output=True,
            text=True,
            timeout=IAP_AUTH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "iap-auth is not installed. Run: airtool install iap-auth"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"iap-auth timed out fetching a token for {base_url}") from e

    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        detail = result.stderr.strip() or "no token returned"
        raise RuntimeError(f"iap-auth failed for {base_url}: {detail}")
    return token


def extract_base_url(url: str) -> str:
    """Return the scheme+host portion of a URL, e.g. 'https://grafana.a.musta.ch'."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Not a full URL (missing scheme/host): {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def parse_ref(url: str, token: str) -> tuple[str, str, str]:
    """
    Extract (base_url, kind, uid) from a Grafana dashboard or folder link.

    `kind` is "dashboard" for `/d/<uid>/...` links or "folder" for
    `/dashboards/f/<uid>/...` links. Short `/goto/<hash>` links are resolved
    to their real target first (one redirect hop) since they don't carry the
    uid in the URL.
    """
    base_url = extract_base_url(url)
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) >= 2 and parts[0] == "d":
        return base_url, "dashboard", parts[1]

    if len(parts) >= 3 and parts[0] == "dashboards" and parts[1] == "f":
        return base_url, "folder", parts[2]

    if len(parts) >= 2 and parts[0] == "goto":
        resolved = _resolve_goto_link(url, token)
        return parse_ref(resolved, token)

    raise ValueError(
        f"Could not recognise URL path {parsed.path!r} as a dashboard "
        "('/d/<uid>/...') or folder ('/dashboards/f/<uid>/...') link."
    )


def _resolve_goto_link(url: str, token: str) -> str:
    """Follow one redirect hop to resolve a short `/goto/<hash>` link."""
    headers = {"Proxy-Authorization": f"Bearer {token}"}
    with httpx.Client(follow_redirects=False, timeout=30.0) as client:
        response = client.get(url, headers=headers)

    location: str | None = (
        response.headers.get("location") if response.is_redirect else None
    )
    if location:
        return urljoin(url, location)

    raise ValueError(
        f"Could not resolve short link {url!r} (HTTP {response.status_code}). "
        "Open it in a browser and pass the resulting URL instead."
    )


def fetch_dashboard(base_url: str, uid: str, token: str) -> dict[str, Any]:
    """Fetch a dashboard's JSON model from the Grafana HTTP API."""
    headers = {"Proxy-Authorization": f"Bearer {token}"}
    response = httpx.get(
        f"{base_url}/api/dashboards/uid/{uid}", headers=headers, timeout=30.0
    )
    response.raise_for_status()

    payload = response.json()
    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, dict):
        raise ValueError(f"Grafana response for uid {uid!r} had no 'dashboard' object.")
    return dashboard


def list_folder_contents(
    base_url: str, folder_uid: str, token: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Return (dashboards, subfolders) found directly inside a folder.

    Not recursive — items in subfolders are not included. Uses the Grafana
    search API's folderUIDs filter, which only returns direct children.
    """
    headers = {"Proxy-Authorization": f"Bearer {token}"}
    response = httpx.get(
        f"{base_url}/api/search",
        headers=headers,
        params={"folderUIDs": folder_uid, "limit": _FOLDER_SEARCH_LIMIT},
        timeout=30.0,
    )
    response.raise_for_status()

    results: list[dict[str, Any]] = response.json()
    dashboards = [r for r in results if r.get("type") == "dash-db"]
    subfolders = [r for r in results if r.get("type") == "dash-folder"]
    return dashboards, subfolders


def save_dashboard(dashboard: dict[str, Any], output_dir: Path, uid: str) -> Path:
    """Write the dashboard JSON model to <output_dir>/<uid>.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{uid}.json"
    path.write_text(json.dumps(dashboard, indent=2, sort_keys=True) + "\n")
    return path


def _download_one(base_url: str, uid: str, token: str, output_dir: Path) -> bool:
    """Fetch and save a single dashboard. Returns True on success."""
    try:
        dashboard = fetch_dashboard(base_url, uid, token)
    except httpx.HTTPStatusError as e:
        print_error(
            f"Grafana API returned {e.response.status_code} for uid {uid!r}: "
            f"{e.response.text}"
        )
        return False
    except (httpx.HTTPError, ValueError) as e:
        print_error(f"Failed to fetch dashboard {uid!r}: {e}")
        return False

    path = save_dashboard(dashboard, output_dir, uid)
    title = dashboard.get("title", uid)
    print_success(f"Saved '{title}' to {path}")
    return True


def _download_folder(
    base_url: str, folder_uid: str, token: str, output_dir: Path
) -> int:
    """Fetch and save every dashboard directly inside a folder. Returns exit code."""
    try:
        dashboards, subfolders = list_folder_contents(base_url, folder_uid, token)
    except httpx.HTTPStatusError as e:
        print_error(
            f"Grafana API returned {e.response.status_code} listing folder "
            f"{folder_uid!r}: {e.response.text}"
        )
        return 1
    except httpx.HTTPError as e:
        print_error(f"Failed to reach {base_url}: {e}")
        return 1

    if subfolders:
        titles = ", ".join(f"'{f.get('title', f.get('uid'))}'" for f in subfolders)
        print_warning(
            f"Skipping {len(subfolders)} subfolder(s) (not recursive): {titles}. "
            "Re-run with a subfolder's link to fetch its dashboards."
        )

    if not dashboards:
        print_warning(f"No dashboards found directly inside folder {folder_uid!r}.")
        return 0

    print_info(f"Found {len(dashboards)} dashboard(s) in folder {folder_uid!r}.")
    failures = sum(
        0 if _download_one(base_url, d["uid"], token, output_dir) else 1
        for d in dashboards
    )

    total = len(dashboards)
    print(f"\nDownloaded {total - failures}/{total} dashboard(s).")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grafana_dashboard_downloader",
        description="Download Grafana dashboard JSON model(s) to local files.",
    )
    parser.add_argument(
        "url",
        help="Grafana dashboard link ('/d/<uid>/...') or folder link "
        "('/dashboards/f/<uid>/...')",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for <uid>.json file(s) (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--token",
        default=None,
        help=f"IAP token (defaults to ${IAP_TOKEN_ENV_VAR}, then a fresh "
        "`iap-auth <base-url>` call)",
    )
    args = parser.parse_args(argv)

    try:
        base_url = extract_base_url(args.url)
    except ValueError as e:
        print_error(str(e))
        return 1

    try:
        token = resolve_token(args.token, base_url)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    try:
        _, kind, uid = parse_ref(args.url, token)
    except ValueError as e:
        print_error(str(e))
        return 1

    if kind == "dashboard":
        print_info(f"Fetching dashboard '{uid}' from {base_url}…")
        return 0 if _download_one(base_url, uid, token, args.output_dir) else 1

    print_info(f"Listing dashboards in folder '{uid}' from {base_url}…")
    return _download_folder(base_url, uid, token, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
