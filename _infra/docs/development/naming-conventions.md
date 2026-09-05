## Context

This file defines the naming conventions and configuration organization for the Matik project infrastructure.

Key requirements:
- Pythonic naming conventions (snake_case)
- Clear file naming patterns
- Centralized variable definitions in kube-gen.yml
- Service-oriented hierarchy for configuration organization

## Configuration Files (_infra/kube/)

### 1. File Naming Conventions (For Configuration Files)

**Pattern:** `<service>-<datasource>-<type>.yml`

| Component | Description | Examples |
|-----------|-------------|----------|
| `service` | Service name or `shared` for multi-service configs | `historian`, `chronicler`, `catalog`, `migrator`, `shared` |
| `datasource` | Data source/integration name (optional) | `incidentio`, `jira`, `github`, `pagerduty`, `greenroom`, `facade` |
| `type` | Type of configuration file | `config`, `secrets_ref`, `deployment` |

**Rules:**
- Use `.yml` extension (not `.yaml`)
- No `matik-` prefix (everything is already in the matik project)
- Shared configs use `shared` as the service name

### 2. Variable Naming Conventions (Pythonic)

All variable names use **snake_case**. Avoid camelCase or PascalCase.

| Category | Convention | Examples |
|----------|------------|----------|
| Simple variables | `lowercase_with_underscores` | `port`, `log_level`, `page_size` |
| Namespaced variables | `namespace_variable_name` | `mysql_endpoint`, `facade_base_url` |
| Boolean flags | `is_*` or `*_enabled` | `is_production`, `iam_enabled`, `connector_enabled` |
| URLs/endpoints | `*_url` or `*_endpoint` | `api_base_url`, `rds_endpoint` |
| Credentials | `*_key`, `*_token`, `*_secret` | `api_key`, `auth_token`, `client_secret` |
| Counts/limits | `*_count`, `*_limit`, `max_*`, `min_*` | `retry_count`, `page_limit`, `max_replicas` |
| Time durations | `*_timeout`, `*_interval`, `*_frequency` | `request_timeout`, `poll_interval`, `sync_frequency` |

### 3. Configuration Hierarchy: Service → Datasource

**Core Principle:** Organize configuration variables by service first, then by datasource/integration within that service.

**Rationale:**
- Groups all configs for a service together - easy to see everything a service needs
- Matches deployment model (1 service = 1 pod = 1 config bundle)
- Natural for "What does historian need to run?" questions
- When deploying a service, all its configs are in one place
- Adding a new datasource to a service means editing one section

### 4. Configuration Placement

**Core Principle:** All configuration variables are defined in `kube-gen.yml`. Files in `files/` only reference these variables via templates.

**Hierarchy:** `common → environment → params → shared/service → datasource → variables`

```bash
kube-gen.yml
├── common
│   ├── all                              # Base config for all environments
│   │   └── params
│   │       ├── shared                   # Variables shared across services
│   │       │   ├── port
│   │       │   ├── log_level
│   │       │   ├── replicas
│   │       │   ├── facade               # Shared LLM config
│   │       │   │   ├── base_url
│   │       │   │   └── default_model
│   │       │   ├── mysql                # Shared database config
│   │       │   │   ├── endpoint
│   │       │   │   └── database
│   │       │   ├── greenroom            # Shared service registry
│   │       │   │   └── host
│   │       │   └── observability        # Shared observability
│   │       │       └── logging_endpoint
│   │       │
│   │       ├── historian                # Service-specific config
│   │       │   ├── incidentio           # Datasource
│   │       │   │   ├── enabled
│   │       │   │   ├── frequency
│   │       │   │   └── page_size
│   │       │   ├── jira                 # Datasource
│   │       │   │   ├── base_url
│   │       │   │   ├── tcmrs            # Sub-datasource
│   │       │   │   └── ops_tickets      # Sub-datasource
│   │       │   └── biztech_github       # Datasource
│   │       │
│   │       ├── chronicler               # Service-specific config
│   │       │   └── webhooks
│   │       │
│   │       ├── catalog                  # Service-specific config
│   │       │   └── mysql
│   │       │
│   │       ├── enigmatologist           # Service-specific config
│   │       │   ├── llm
│   │       │   └── prompts
│   │       │
│   │       └── migrator                 # Service-specific config
│   │           └── mysql, job
│   │
│   ├── production                       # Production overrides (same structure)
│   │   └── params
│   │       ├── shared
│   │       │   ├── iam_role
│   │       │   └── facade
│   │       │       └── base_url (override)
│   │       └── historian
│   │           └── incidentio
│   │               └── frequency (override)
│   │
│   ├── staging                          # Staging overrides (same structure)
│   │   └── params
│   │       └── ...
│   │
│   └── sandbox                      # Sandbox overrides (same structure)
│       └── params
│           └── ...

files/ (Reference-only, no hardcoded values)
├── historian-incidentio-connector.yml   # {{ .Env.Params.historian.incidentio.* }}
├── historian-jira-connector.yml         # {{ .Env.Params.historian.jira.* }}
├── shared-facade-config.yml             # {{ .Env.Params.shared.facade.* }}
└── ...

apps/ (Deployment specifications)
├── historian-deployment.yml
├── chronicler-deployment.yml
└── ...
```

### 5. kube-gen.yml Complete Structure

```yaml
common:
  # =========================================================================
  # ALL - Base config for all environments
  # =========================================================================
  all:
    params:
      # ---------------------------------------------------------------------
      # SHARED - Variables shared across all services
      # ---------------------------------------------------------------------
      shared:
        port: 8080
        log_level: info
        min_replicas: 1
        max_replicas: 3

        observability:
          logging_rate_limit_per_pod: 10
          logging_endpoint: logs-shared-beech
          telescope_enabled: true

        mysql:
          database: matik
          max_open_connections: 25
          max_idle_connections: 5

        facade:
          resource_bucket: production
          api_version: "2024-12-01-preview"
          default_model: matik-sandbox-gpt-4o

        greenroom:
          host: developers.a.musta.ch

      # ---------------------------------------------------------------------
      # HISTORIAN - Service config with datasources
      # ---------------------------------------------------------------------
      historian:
        incidentio:
          enabled: true
          frequency: 5m
          page_size: 100
          api_key: "{{ .App.Secrets.incidentio.api_key }}"

        jira:
          base_url: https://jirarest-stage.airbnb.biz
          username: "{{ .App.Secrets.jira.username }}"
          password: "{{ .App.Secrets.jira.password }}"
          pagination_max_results: 200

          tcmrs:
            enabled: true
            frequency: 50m
            jql_query: "project = TCMR AND ..."

          ops_tickets:
            enabled: false
            frequency: 5m

        biztech_github:
          enabled: true
          frequency: 45m
          page_size: 100
          app_private_key: "{{ .App.Secrets.github.app_private_key }}"

      # ---------------------------------------------------------------------
      # CHRONICLER - Service config
      # ---------------------------------------------------------------------
      chronicler:
        webhooks:
          enabled: true
          max_payload_size: 1048576
          timeout_seconds: 30

      # ---------------------------------------------------------------------
      # CATALOG - Service config
      # ---------------------------------------------------------------------
      catalog:
        mysql:
          pool_size: 10
          query_timeout_seconds: 30

      # ---------------------------------------------------------------------
      # ENIGMATOLOGIST - Service config
      # ---------------------------------------------------------------------
      enigmatologist:
        llm:
          model: gpt-4o
          max_tokens: 4096
          temperature: 0.7

        prompts:
          summarization: "Summarize the following incident..."
          classification: "Classify this incident into categories..."

      # ---------------------------------------------------------------------
      # MIGRATOR - Service config
      # ---------------------------------------------------------------------
      migrator:
        mysql:
          migration_timeout_seconds: 300
          lock_timeout_seconds: 60

        job:
          backoff_limit: 3
          ttl_seconds_after_finished: 86400

  # =========================================================================
  # PRODUCTION - Production environment overrides (same structure)
  # =========================================================================
  production:
    params:
      shared:
        iam_role: matik-production
        secrets_databag_name: production
        mysql:
          endpoint: vpce-xxx-production.vpce.amazonaws.com
        facade:
          base_url: http://llm-fusion-hub-production:11000/...
          default_model: matik-production-gpt-4o
        greenroom:
          host: greenroom-production.greenroom-production:7007

      historian:
        jira:
          base_url: https://jirarest.airbnb.biz

  # =========================================================================
  # STAGING - Staging environment overrides (same structure)
  # =========================================================================
  staging:
    params:
      shared:
        iam_role: matik-staging
        secrets_databag_name: staging
        mysql:
          endpoint: vpce-xxx-staging.vpce.amazonaws.com
        facade:
          default_model: matik-staging-gpt-4o

  # =========================================================================
  # DEVELOPMENT - Development environment overrides (same structure)
  # =========================================================================
  sandbox:
    params:
      shared:
        iam_role: matik-sandbox
        secrets_databag_name: dev
        mysql:
          endpoint: vpce-xxx-sandbox.vpce.amazonaws.com
        facade:
          default_model: matik-sandbox-gpt-4o

      historian:
        incidentio:
          frequency: 60m  # Slower polling in dev
        biztech_github:
          frequency: 60m
```

### 6. Config File Templates (files/)

Files only reference variables - no hardcoded values.

**Standard Header:**
```yaml
# <filename>
# Services: <comma-separated list of services using this config>
# Description: <brief description of purpose>
---
```

**Example: historian-incidentio-connector.yml**
```yaml
# historian-incidentio-connector.yml
# Services: historian
# Description: Incident.io connector configuration
---
connector:
  enabled: {{ .Env.Params.historian.incidentio.enabled }}
  frequency: {{ .Env.Params.historian.incidentio.frequency }}
  page_size: {{ .Env.Params.historian.incidentio.page_size }}
  api_key: {{ .Env.Params.historian.incidentio.api_key }}
```

**Example: shared-facade-config.yml**
```yaml
# shared-facade-config.yml
# Services: historian, catalog, enigmatologist
# Description: LLM Fusion Hub (Facade) configuration
---
facade:
  base_url: {{ .Env.Params.shared.facade.base_url }}
  resource_bucket: {{ .Env.Params.shared.facade.resource_bucket }}
  default_model: {{ .Env.Params.shared.facade.default_model }}
  api_version: {{ .Env.Params.shared.facade.api_version }}
```

**Example: shared-mysql-config.yml**
```yaml
# shared-mysql-config.yml
# Services: catalog, migrator
# Description: MySQL database configuration
---
mysql:
  endpoint: {{ .Env.Params.shared.mysql.endpoint }}
  database: {{ .Env.Params.shared.mysql.database }}
  iam_role: {{ .Env.Params.shared.iam_role }}
  max_open_connections: {{ .Env.Params.shared.mysql.max_open_connections }}
  max_idle_connections: {{ .Env.Params.shared.mysql.max_idle_connections }}
```

### 7. Final Directory Structure

```bash
_infra/kube/
├── kube-gen.yml                              # ALL variables defined here
│                                             # Hierarchy: common → env → params → shared/service → datasource
│
├── apps/                                     # Deployment specifications only
│   ├── historian-deployment.yml
│   ├── chronicler-deployment.yml
│   ├── catalog-deployment.yml
│   ├── enigmatologist-deployment.yml
│   └── migrator-deployment.yml
│
├── files/                                    # Reference-only config files
│   ├── historian-incidentio-connector.yml    # {{ .Env.Params.historian.incidentio.* }}
│   ├── historian-jira-connector.yml          # {{ .Env.Params.historian.jira.* }}
│   ├── historian-github-connector.yml        # {{ .Env.Params.historian.biztech_github.* }}
│   ├── chronicler-webhook-config.yml         # {{ .Env.Params.chronicler.webhooks.* }}
│   ├── enigmatologist-llm-config.yml         # {{ .Env.Params.enigmatologist.llm.* }}
│   ├── shared-facade-config.yml              # {{ .Env.Params.shared.facade.* }}
│   ├── shared-mysql-config.yml               # {{ .Env.Params.shared.mysql.* }}
│   ├── shared-greenroom-config.yml           # {{ .Env.Params.shared.greenroom.* }}
│   └── shared-observability-config.yml       # {{ .Env.Params.shared.observability.* }}
│
├── images/                                   # Docker image specs
│   ├── historian.Dockerfile
│   ├── chronicler.Dockerfile
│   ├── catalog.Dockerfile
│   ├── enigmatologist.Dockerfile
│   └── migrator.Dockerfile
│
└── containers/                               # Init/sidecar containers
    └── 10-init-catalog-wait.yml
```

## App Files (matik/)

### Utils

The `common/utils` folder hosts the utility functions and models used across different services.
The naming conventions for utility files are as follows:
- Use descriptive names that reflect the functionality provided by the utility.
- Follow snake_case for file names.
- Group related utilities into a single file when appropriate.

Examples:
- `common/utils/datetime_utils.py` for date and time related functions.

### Functions

Function names should be descriptive and follow snake_case conventions.
Example:
- `parse_timestamp_to_utc` for a function that parses timestamps to UTC datetime objects.

### Classes

Class names should use PascalCase and be descriptive of their purpose.
Example:
- `GHEOrganization` for a class representing a GitHub Enterprise Organization.
- `DatetimeUtils` for a class encapsulating datetime utility methods.

## Tests (matik/)

Tests should be located in the same directory as the code they are testing, with filenames ending in `_test.py`.
Examples:
- `common/utils/datetime_utils_test.py` for testing datetime utilities.

The test file should contain a separate class for each function being tested, with methods named to reflect the specific test cases.
The class name should start with `Test` followed by the function name in PascalCase.
Examples:
```python
class TestParseTimestampToUtc:
    """Test suite for parse_timestamp_to_utc function."""

    def test_none_input(self) -> None:
        """Test that None input returns None."""
        result = parse_timestamp_to_utc(None)
        assert result is None
```
