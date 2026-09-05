# Configuration

## Loading Config

Config files are loaded from `/app/config/` by default. Just pass the filename:

```python
from common.config import load_config

config = load_config("matik-historian-config.yaml")
```

## Accessing Config Values

```python
# Database
config.mysql.endpoint
config.mysql.username

# Service-specific
config.common.environment
config.common.log_level
config.jira.base_url
config.incidentio.api_key
```

## Config Files

Service config files are in [kube/files](../../kube/files/):
- `matik-historian-config.yaml`
- `matik-chronicler-config.yaml`
- `matik-api-config.yaml`
- `matik-migrator-config.yaml`

## Environment Variable Substitution

Config values can reference environment variables using `${VAR_NAME}` syntax:

```yaml
incidentio:
  api_key: ${INCIDENTIO_API_KEY}
```

### Handling Unset Environment Variables

When an env var is not set, `envsubst` replaces it with an empty string, which YAML parses as `None`. Pydantic models should handle this with `field_validator`:

```python
from pydantic import BaseModel, Field, field_validator

class TelescopeConfig(BaseModel):
    enabled: bool | None = Field(default=False)

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, v: bool | None) -> bool:
        """Convert None to False (handles unset env vars)."""
        return False if v is None else v
```

## Merging Configs

Use `merge_config()` to combine multiple config files:

```python
from common.config import load_config, merge_config

base = load_config("base.yaml")
merged = merge_config(base, "additional.yaml")
```

The additional config values are merged into the base config. If the same top-level key exists in both, the additional config takes precedence.
