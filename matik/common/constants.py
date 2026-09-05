"""Shared constants for the Matik platform."""

# HTTP Headers
TASK_ID_HEADER = "X-Task-ID"

# Phase 1c service-to-service signature (see common/utils/service_auth.py)
SERVICE_TIMESTAMP_HEADER = "X-Matik-Service-Timestamp"
SERVICE_SIGNATURE_HEADER = "X-Matik-Service-Signature"

# API
API_VERSION = "1.0.0"
API_V1_PREFIX = "/v1"

# Telescope Metrics
TELESCOPE_TENANT_LABEL = "telescope_tenant_id"
TELESCOPE_ORG_HEADER = "X-Scope-OrgID"
DEFAULT_TELESCOPE_TIMEOUT_SECONDS = 30
DEFAULT_OTEL_ENDPOINT = "http://127.0.0.1:4318"
OTEL_METRICS_PATH = "/v1/metrics"

# Artifactory
ARTIFACTORY_SUMMARY_FILE_PATH = "infra_indexer/tmp/summary.yml"

# Source base URLs (used to compute direct links to source records at runtime)
INCIDENTIO_BASE_URL = "https://app.incident.io/airbnb/incidents"
JIRA_BROWSE_URL = "https://jira.airbnb.biz/browse"
GHE_BASE_URL = "https://github.airbnb.biz"
