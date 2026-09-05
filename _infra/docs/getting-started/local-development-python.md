# Local Development Guide (Python)

This guide provides step-by-step instructions to set up and run the Matik Python application locally.

## Prerequisites

- Python 3.13+
  - Confirm internally supported version before upgrading [here](https://git.musta.ch/airbnb/kube-images/blob/master/base/ubuntu-22.04/scripts/install-python.sh#L126).
  - The project has a pinned Python version specified in the `.python-version` file at the repository root. uv will automatically use this version when running commands.
- uv (Python package and project manager)

## What is uv?

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package and project manager, written in Rust. It serves as a drop-in replacement for pip, pip-tools, pipx, poetry, pyenv, virtualenv, and more.

**Key Features:**

- **Fast**: 10-100x faster than pip and pip-tools
- **All-in-one tool**: Manages Python versions, virtual environments, dependencies, and project workflows
- **pip-compatible**: Works with existing `requirements.txt` and `pyproject.toml` files
- **Reliable**: Produces consistent, reproducible environments
- **Modern**: Built with Rust for performance and reliability

## Installing uv

### macOS and Linux

Install using the standalone installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or using Homebrew:

```bash
brew install uv
```

### Verify Installation

Confirm uv is installed correctly:

```bash
uv --version
```

You should see output showing the installed version, for example:

```
uv 0.9.22 (82a6a66b8 2026-01-06)
```

### Alternative Installation Methods

For other installation methods and detailed instructions, see the [official uv documentation](https://docs.astral.sh/uv/getting-started/installation/).

### Managing Dependencies

See the [Dependency Management Guide](../development/dependencies.md).

## Code Quality Tools

The project uses **Ruff** for linting and formatting, and **mypy** for static type checking. Both are included as dev dependencies and have dedicated configuration files.

### Ruff

[Ruff](https://docs.astral.sh/ruff/) is an extremely fast Python linter and formatter, written in Rust. It replaces Flake8, isort, Black, and many other tools.

**Configuration:** `matik/ruff.toml`

**Running Ruff:**

```bash
# Check for linting issues
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .

# Format code
uv run ruff format .

# Check formatting without making changes
uv run ruff format --check .
```

### mypy

[mypy](https://mypy.readthedocs.io/) is a static type checker for Python that helps catch type errors before runtime.

**Configuration:** `matik/mypy.ini`

**Running mypy:**

```bash
# Type check all code (uses config file automatically)
uv run mypy

# Type check with explicit config
uv run mypy --config-file mypy.ini
```

## Running with Docker Compose

### Quick Start

```bash
cd matik/

# 1. Setup environment
cp .env.example .env.local
# Edit .env.local with your API keys and credentials

# 2. Start all services
docker compose up -d --build

# 3. Verify
docker compose ps
```

### Running Jobs

Jobs (e.g., historian crawlers) are one-off tasks defined with `profiles` so they don't start automatically with `docker compose up`.

```bash
# Run a specific job
docker compose run historian_incidentio
docker compose run historian_jira
docker compose run historian_biztech_github
```

### Common Operations

```bash
# View logs
docker compose logs -f api

# Restart service
docker compose restart api

# Stop all
docker compose down

# Reset everything (including database)
docker compose down -v && docker compose up -d --build
```

### Best Practices

**Configuration:**
- Secrets/credentials go in `.env.local` (gitignored)
- App settings go in `local-configs/*.yaml` (committed)
- Config files use `${VAR}` placeholders, substituted at container startup

**Security:**
- Never commit `.env.local`
- Keep `.env.example` updated with placeholder values
- Services run as non-root user (UID 1000)

**Development:**
- Config changes only need `docker compose restart` (no rebuild)
- Code changes need `docker compose up -d --build`

## Database Migrations

The project uses **Alembic** for database migrations. Alembic and related dependencies (SQLAlchemy, PyMySQL) are included in the dev dependency group, so they're installed automatically with `uv sync --dev`.

### Running Migrations

For day-to-day migration operations, see the [Database Management Guide](../operations/db-management.md), which covers:
- Creating new migrations
- Running migrations locally (docker-compose)
- Running migrations manually against remote databases
- Troubleshooting migration issues
