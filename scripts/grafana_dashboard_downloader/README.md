# Grafana Dashboard Downloader

Downloads Grafana dashboard JSON model(s) to local files, given a dashboard
link or a folder link. Output matches the format of the dashboard JSON files
already checked in under
`matik/local-configs/grafana/provisioning/dashboards/` (2-space indent,
alphabetically sorted keys, just the `dashboard` object — not the `meta`
wrapper Grafana's API returns it in).

## Quick start

Grafana is IAP-protected, so this follows the same auth pattern as other
local-dev IAP calls in this repo (see `matik/scripts/run_eval.sh` and
`_infra/docs/runbooks/api-availability-low.md`): a token from `iap-auth
<base-url>`, sent as `Proxy-Authorization: Bearer <token>`.

If `iap-auth` is installed (`airtool install iap-auth`), no manual token step
is needed — just run it with a link copied from the browser:

```bash
cd scripts

# A single dashboard
uv run python -m grafana_dashboard_downloader \
    https://grafana.a.musta.ch/d/matik-chronicler/matik-chronicler-dashboard?orgId=1

# Every dashboard directly inside a folder
uv run python -m grafana_dashboard_downloader \
    https://grafana.a.musta.ch/dashboards/f/<folder-uid>/<folder-slug>?orgId=1
```

To reuse an existing token instead (IAP tokens are short-lived, ~1h), set
`$IAP_TOKEN` or pass `--token`:

```bash
export IAP_TOKEN="$(iap-auth https://grafana.a.musta.ch)"
```

This writes one `<uid>.json` per dashboard to `downloads/` next to this
script (`scripts/grafana_dashboard_downloader/downloads/`) — anchored to the
script's own location, not wherever you happen to run the command from.
Override with `--output-dir`, e.g. to write straight into
`matik/local-configs/grafana/provisioning/dashboards/`.

Folder downloads are **not recursive**: dashboards inside subfolders are
skipped, and the subfolder titles are printed so you can re-run against each
subfolder's link if you need those too.

Short `/goto/<hash>` links are also accepted (for either a dashboard or a
folder); they're resolved to their real target with one redirect hop before
fetching.

## Options

```
uv run python -m grafana_dashboard_downloader <url> [--output-dir DIR] [--token TOKEN]
```

- `--output-dir`: where to write `<uid>.json` file(s) (default:
  `scripts/grafana_dashboard_downloader/downloads/`)
- `--token`: IAP token (defaults to `$IAP_TOKEN`, then a fresh `iap-auth
  <base-url>` call)

## Prerequisites

```bash
cd scripts && uv sync   # installs httpx and other deps
airtool install iap-auth
```
