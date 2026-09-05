#!/usr/bin/env bash
# Updates the last-modified.txt file with current UTC timestamp
# when the Grafana dashboard JSON is modified.
#
# This script is run by pre-commit when matik-metrics.json changes.

set -euo pipefail

DASHBOARD_FILE="matik/local-configs/grafana/provisioning/dashboards/matik-metrics.json"
TIMESTAMP_FILE="matik/local-configs/grafana/provisioning/dashboards/last-modified.txt"

# Check if the dashboard file exists
if [[ ! -f "$DASHBOARD_FILE" ]]; then
    echo "Dashboard file not found: $DASHBOARD_FILE"
    exit 1
fi

# Only proceed if the dashboard file is actually in the staged changes
if ! git diff --cached --name-only | grep -q "^${DASHBOARD_FILE}$"; then
    echo "Dashboard file not staged, skipping timestamp update"
    exit 0
fi

# Get current UTC timestamp
UTC_TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# Update the timestamp file
echo "$UTC_TIMESTAMP" > "$TIMESTAMP_FILE"

# Stage the timestamp file so it's included in the commit
git add "$TIMESTAMP_FILE"

echo "Updated $TIMESTAMP_FILE with: $UTC_TIMESTAMP"
