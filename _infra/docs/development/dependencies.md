# Dependency Management

This guide covers how to add and manage Python dependencies in the Matik project using uv and pyproject.toml.

## Overview

Matik uses [uv](https://github.com/astral-sh/uv) for dependency management with a single `pyproject.toml` file and optional dependencies per service. This approach provides:

- **Single source of truth** - One `pyproject.toml` for all services
- **Smaller production images** - Each service only installs what it needs
- **Reproducible builds** - `uv.lock` ensures consistent dependency versions

## Project Structure

```
matik/
├── pyproject.toml      # Dependency definitions
├── uv.lock             # Locked dependency versions (auto-generated)
├── historian/          # Service with optional deps
├── chronicler/         # Service with optional deps
├── api/                # Service with optional deps
├── migrator/           # Service with optional deps
└── common/             # Shared code (uses base deps)
```

## Dependency Types

### Shared Dependencies

Dependencies used by all services go in the main `dependencies` list:

```toml
[project]
dependencies = [
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "sqlalchemy>=2.0.0",
]
```

**When to use:** Libraries used by `common/` or multiple services.

### Service-Specific Dependencies (Optional Extras)

Dependencies only needed by a specific service go in `optional-dependencies`:

```toml
[project.optional-dependencies]
historian = [
    "requests>=2.31.0",
]

chronicler = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
]

api = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
]

migrator = [
    "alembic>=1.13.0",
]
```

**When to use:** Libraries only needed by one service.

## Adding Dependencies

### Step 1: Edit pyproject.toml

Add the dependency to the appropriate section:

```toml
# For shared dependencies
[project]
dependencies = [
    "httpx>=0.27.0",
    "new-package>=1.0.0",  # Add here
]

# For service-specific dependencies
[project.optional-dependencies]
historian = [
    "requests>=2.31.0",
    "another-package>=2.0.0",  # Add here
]
```

### Step 2: Update the Lock File

```bash
cd matik
uv lock
```

This resolves all dependencies and updates `uv.lock`.

### Step 3: Install Locally (Optional)

Only needed for local development outside Docker (IDE autocomplete, local tests, etc.):

```bash
# Install all dependencies (for local development)
uv sync --extra historian --extra chronicler --extra api --extra migrator

# Or install specific service dependencies only
uv sync --extra historian
```

### Step 4: Rebuild Docker Images

```bash
# Local development
docker compose build

# Or rebuild specific service
docker compose build api
```

## Removing Dependencies

1. Remove the line from `pyproject.toml`
2. Run `uv lock` to update the lock file
3. Run `uv sync` to update your local environment
4. Rebuild Docker images

## Checking Installed Dependencies

```bash
# List all installed packages
uv pip list

# Check specific package
uv pip show package-name

# Check outdated packages
uv pip list --outdated
```

## How Docker Builds Use Dependencies

### Local Development (Dockerfile)

The local Dockerfile installs **all** optional dependencies for convenience:

```dockerfile
RUN uv sync --frozen --no-dev \
    --extra historian --extra chronicler --extra api --extra migrator
```

This means all services work with one image locally.

### Production (Service-Specific Dockerfiles)

Production Dockerfiles in `_infra/kube/images/` install only what each service needs:

```dockerfile
# historian.Dockerfile
RUN uv sync --frozen --no-dev --extra historian

# api.Dockerfile
RUN uv sync --frozen --no-dev --extra api
```

This keeps production images smaller and more secure.

## Best Practices

### 1. Pin Minimum Versions

Use `>=` to specify minimum compatible versions:

```toml
"fastapi>=0.115.0"  # Good - allows compatible updates
"fastapi==0.115.0"  # Avoid - too restrictive
"fastapi"           # Avoid - no version constraint
```

### 2. Keep Shared Dependencies Minimal

Only add to shared `dependencies` if truly needed by multiple services or `common/`.

### 3. Review Before Adding

Before adding a new dependency:
- Check if an existing dependency already provides the functionality
- Consider the package's maintenance status and security
- Evaluate the impact on image size

### 4. Update Lock File in Commits

Always commit `uv.lock` changes along with `pyproject.toml` changes to ensure reproducible builds.

## Troubleshooting

### Lock file out of sync

```bash
# Error: Lock file is out of date
uv lock
```

### Dependency conflicts

```bash
# See detailed resolution
uv lock -v
```

### Clear cache and reinstall

```bash
uv cache clean
uv sync --reinstall
```

## Quick Reference

| Task | Command |
|------|---------|
| Add dependency | Edit `pyproject.toml`, then `uv lock` |
| Update lock file | `uv lock` |
| Install all deps locally | `uv sync --extra historian --extra chronicler --extra api --extra migrator` |
| Install specific service deps | `uv sync --extra api` |
| Rebuild Docker images | `docker compose build` |
| List installed packages | `uv pip list` |
| Check outdated packages | `uv pip list --outdated` |
