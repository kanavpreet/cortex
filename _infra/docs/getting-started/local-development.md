# Local Development Guide

This guide provides step-by-step instructions to set up and run the Matik application locally using Docker and Docker Compose.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+

## Configuration Files Overview

Matik uses a split configuration approach for local development:

| File/Directory | Purpose | Committed to Git? | Example Content |
|---------------|---------|-------------------|-----------------|
| `.env` | Secrets, credentials, environment-specific values | ❌ No | API keys, passwords, tokens |
| `.env.example` | Template showing required variables | ✅ Yes | Variable names with placeholder values |
| `.env.local` | Your local secrets and credentials | ❌ No | Actual API keys, passwords, tokens |
| `local-configs/*.yaml` | Application behavior, integration settings | ✅ Yes | Static config + `${VAR}` placeholders |
| `docker-compose.yaml` | Service definitions and volume mounts | ✅ Yes | Container orchestration |
| `entrypoint.sh` | Processes configs at container startup | ✅ Yes | Sources `.env`, runs `envsubst` |

**Key Concept:** Config files use `${VARIABLE_NAME}` placeholders that get replaced with actual values from `.env` at runtime.

## Quick Start

### 1. Configure Environment Variables

Copy the example environment file to create your local configuration:

```bash
cp .env.example .env.local
```

Edit `.env.local` and fill in your actual API keys and credentials:

- **Database**: MySQL credentials (pre-configured for local dev)
- **Incident.io**: API key for incident data
- **PagerDuty**: API key for alert data
- **JIRA**: Base URL, username, password, and JQL queries
- **AWS**: Region, access keys, SQS queue URL
- **LLM Providers**: OpenAI/Anthropic API keys, IAP token for Facade

**Important:** The `.env.local` file is used in two ways:

1. Docker Compose reads it for container configuration
2. The entrypoint script sources it and uses values to populate `local-configs/*.yaml` files

### 1.1. Configure Application Settings (Optional)

Application-specific settings are managed in the `local-configs/` directory. These YAML files mirror the production Kubernetes configuration and support environment variable substitution.

**Example:** `local-configs/matik-historian-config.yaml`

```yaml
common:
  port: 8080
  baseUrl: ${BASE_URL}
  user: root
  password: ${DB_PASSWORD}

jira:
  baseUrl: ${JIRA_BASE_URL}
  username: ${JIRA_USERNAME}

pagerduty:
  apiKey: ${PAGERDUTY_API_KEY}
```

**How it works:**

- Environment variables from `.env.local` are substituted into config files at container startup
- Syntax: `${VARIABLE_NAME}` is replaced with the actual value from `.env.local`
- This mirrors production where kubegen mounts config files with environment-specific values

### 2. Build and Start Services

Build the Docker images and start all services:

```bash
docker-compose up -d --build
```

This will start:

- **MySQL 8.0** database on port 3306
- **Historian** service (background worker)
- **Chronicler** service on port 8080 (webhooks)
- **Enigmatologist** service on port 8081 (LLM analysis)
- **Historian Dispatcher** service (task dispatcher)

### 3. Verify Services are Running

```bash
docker-compose ps
```

Check logs for any service:

```bash
docker-compose logs -f historian
docker-compose logs -f chronicler
docker-compose logs -f catalog
docker-compose logs -f correlator
docker-compose logs -f mysql
```

### 4. Database Initialization

The MySQL database will automatically:

- Create the `matik` database
- Run migrations from `common/migrations/` on first startup

To verify the database is ready:

```bash
docker-compose exec mysql mysql -u matik_user -pmatik_password -e "SHOW DATABASES;"
docker-compose exec mysql mysql -u matik_user -pmatik_password matik -e "SHOW TABLES;"
```

### 5. Access Database UI (Adminer)

Open your browser and navigate to:

```
http://localhost:8082
```

**Login credentials:**

- **System**: MySQL
- **Server**: mysql
- **Username**: matik_user (or use your custom `MYSQL_USER`)
- **Password**: matik_password (or use your custom `MYSQL_PASSWORD`)
- **Database**: matik

Adminer provides a web-based interface to:

- Browse tables and data
- Run SQL queries
- Export/import data
- Manage database schema
- View table structures and relationships

#### Alternative: Direct MySQL CLI Access

```bash
docker-compose exec mysql mysql -u matik_user -pmatik_password matik
```

## Common Operations

### Stop Services

```bash
docker-compose down
```

### Stop and Remove All Data (including database)

```bash
docker-compose down -v
```

### Restart a Specific Service

```bash
docker-compose restart historian
```

### View Service Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f chronicler

# Last 100 lines
docker-compose logs --tail=100 historian
```

### Execute Commands Inside a Container

```bash
# Access MySQL database
docker-compose exec mysql mysql -u matik_user -pmatik_password matik

# Shell into a service container
docker-compose exec historian sh
```

### Rebuild After Code Changes

```bash
docker-compose up -d --build
```

### Scale Services (if needed)

```bash
docker-compose up -d --scale historian=3
```

## Architecture

### Services Overview

| Service | Port | Purpose |
|---------|------|---------|
| **mysql** | 3306 | MySQL 8.0 database (Amazon RDS compatible) |
| **adminer** | 8082 | Database management UI (web-based) |
| **historian** | - | Background service that catalogs past events |
| **chronicler** | 8080 | Webhook server for real-time event ingestion |
| **catalog** | 8081 | API server |
| **correlator** | - | Correlates events together |

### Network

All services run on a dedicated bridge network `matik-network` and can communicate using service names as hostnames.

### Volumes

- `mysql_data`: Persistent storage for MySQL database
- `local-configs/`: Mounted as `/config` (read-only source for templates)
- `.env.local`: Mounted as `/app/.env` (read-only source for variables)

**Important:** Applications should read config from `/app/config/` (not `/config/`), as this is where the processed files with substituted variables are written.

## Configuration System

Matik uses a dual-configuration system that mirrors production Kubernetes deployments:

### Configuration Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Container Startup                          │
├─────────────────────────────────────────────────────────────────┤
│  1. entrypoint.sh runs                                          │
│  2. Sources /app/.env (mounted from .env.local)                │
│  3. Reads /config/*.yaml files (read-only mount)                │
│  4. Processes with envsubst - replaces ${VAR} placeholders      │
│  5. Writes processed configs to /app/config/ (writable)         │
│  6. Starts the application with processed config                │
└─────────────────────────────────────────────────────────────────┘
```

### Environment Variables (.env.local)

The `.env.local` file contains all sensitive credentials and environment-specific values:

- API keys (Incident.io, PagerDuty, JIRA, AWS)
- Database credentials
- Service endpoints
- Feature flags

**Location:** Repository root (not committed to Git)

### Application Config Files (local-configs/)

The `local-configs/` directory contains structured YAML configuration files that define:

- Service behavior and settings
- Integration configurations
- Business logic parameters

**Location:** `local-configs/` directory (committed to Git)

**Files:**

- `matik-historian-config.yaml`: Configuration for historian service
- `matik-chronicler-config.yaml`: Configuration for chronicler service (if needed)
- `matik-enigmatologist-config.yaml`: Configuration for enigmatologist service (if needed)
- `facade-config.yaml`: Configuration for facade client (if needed)

### Variable Substitution

Config files use `${VARIABLE_NAME}` syntax for dynamic values:

```yaml
# local-configs/matik-historian-config.yaml
jira:
  baseUrl: ${JIRA_BASE_URL}      # From .env
  username: ${JIRA_USERNAME}      # From .env
  password: ${JIRA_PASSWORD}      # From .env
  maxResults: 200                 # Static value

pagerduty:
  apiKey: ${PAGERDUTY_API_KEY}   # From .env
  retryAttempts: 3                # Static value
```

When the container starts, the entrypoint script (`entrypoint.sh`) uses `envsubst` to replace placeholders with actual values from `.env.local`.

### Production Parity

This configuration approach matches production:

| Environment | Config Mount | Variable Source | Processor |
|------------|--------------|-----------------|-----------|
| **Production** | kubegen file mounts | Kubernetes secrets/configmaps | kubegen |
| **Local Dev** | Docker volume mounts | `.env.local` file | `envsubst` via entrypoint.sh |

**Benefits:**

- ✅ Same config file structure in all environments
- ✅ Easy to test production config changes locally
- ✅ Clear separation: secrets in `.env.local`, structure in YAML
- ✅ No code changes needed for configuration updates

## Development Workflow

### Make Code Changes

1. Edit your Go files
2. Rebuild and restart affected service:

   ```bash
   docker-compose up -d --build historian
   ```

### Make Configuration Changes

#### Option 1: Edit environment variables (secrets/credentials)


1. Edit `.env.local` file with new values
2. Restart services (no rebuild needed):

   ```bash
   docker-compose restart historian
   ```

#### Option 2: Edit application config (structure/behavior)


1. Edit `local-configs/*.yaml` files
2. Restart services (no rebuild needed):

   ```bash
   docker-compose restart historian
   ```

**Why no rebuild?** Config files and `.env.local` are mounted as volumes. Changes are picked up on container restart because the entrypoint script re-processes them.

### Run Tests

```bash
# Run tests on host (requires Go)
go test ./...

# Or build and run tests in container
docker-compose run --rm historian go test ./...
```

### Access Database for Debugging

#### Option 1: Adminer Web UI (Recommended)

Open http://localhost:8082 in your browser for a graphical interface to:

- Browse all tables and data
- Run SQL queries with syntax highlighting
- Export data in multiple formats
- View table structures and indexes

#### Option 2: MySQL CLI

```bash
# MySQL CLI
docker-compose exec mysql mysql -u matik_user -pmatik_password matik

# Example query
docker-compose exec mysql mysql -u matik_user -pmatik_password matik \
  -e "SELECT incident_id, severity, status FROM incidentio_incidents LIMIT 10;"
```

## Troubleshooting

### Services Won't Start

Check logs for errors:

```bash
docker-compose logs
```

### Database Connection Issues

Ensure MySQL is healthy:

```bash
docker-compose ps mysql
docker-compose logs mysql
```

### Port Conflicts

If ports 3306, 8080, 8081, or 8082 are in use, edit `.env`:

```env
MYSQL_PORT=3307
CHRONICLER_PORT=8090
ENIGMATOLOGIST_PORT=8091
ADMINER_PORT=8092
```

### Reset Everything

```bash
docker-compose down -v
docker-compose up -d --build
```

### Permission Issues

The services run as non-root user (UID 1000). If you see permission errors, check file ownership.

### Configuration Issues

**Problem:** Service starts but can't find configuration

Check that config files are mounted and processed correctly:

```bash
# Check source configs (read-only templates)
docker-compose exec historian ls -la /config

# Check processed configs (with substituted variables)
docker-compose exec historian ls -la /app/config
docker-compose exec historian cat /app/config/matik-historian-config.yaml
```

**Problem:** Environment variables not being substituted

Verify `.env.local` file is mounted and readable:

```bash
docker-compose exec historian ls -la /app/.env
docker-compose exec historian cat /app/.env
```

If file is missing, ensure `.env.local` exists in the repository root:

```bash
ls -la .env.local
```

Check entrypoint logs:

```bash
docker-compose logs historian | grep "entrypoint"
```

**Problem:** Missing environment variable values

Ensure all variables used in `local-configs/*.yaml` are defined in `.env.local`:

```bash
# Check for undefined variables in processed config
docker-compose exec historian cat /app/config/matik-historian-config.yaml | grep '\${'
```

If you see `${VARIABLE_NAME}` still present (not substituted), add the variable to `.env.local`.

**Note:** Your application code should read from `/app/config/` (processed configs), not from `/config/` (read-only templates).

## Production Considerations

This Docker setup is designed for **local development**. For production:

1. Use Amazon RDS for MySQL instead of containerized MySQL
2. Use AWS Secrets Manager for credentials (not `.env.local` files)
3. Configure proper resource limits in docker-compose.yaml
4. Enable TLS/SSL for service communication
5. Set up proper logging and monitoring
6. Use orchestration platforms (ECS, Kubernetes, etc.)

## Best Practices

### Security

1. **Never commit `.env.local`** - it contains secrets (already in `.gitignore`)
2. **Keep `.env.example` updated** - document all required variables when adding new ones (but use placeholder values)
3. **Use read-only mounts** - config and env files are mounted with `:ro` flag
4. **Non-root user** - services run as UID 1000 for security

### Configuration Management

1. **Separate concerns**:
   - Secrets/credentials → `.env.local` file (gitignored)
   - Variable templates → `.env.example` file (committed)
   - Application structure → `local-configs/*.yaml` files (committed)
2. **Test config changes** - update `local-configs/*.yaml` to test production config locally
3. **Use variable substitution** - leverage `${VAR}` syntax in YAML for dynamic values
4. **Mirror production** - keep `local-configs/` structure aligned with `_infra/kube/files/`

### Development

1. **Multi-stage builds** - already configured in Dockerfile for optimal image size
2. **Layer caching** - `go.mod` and `go.sum` are copied separately for faster builds
3. **Health checks** - MySQL has health checks configured, services wait for DB ready

## Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)
- [MySQL Docker Image](https://hub.docker.com/_/mysql)
- [Go Docker Best Practices](https://docs.docker.com/language/golang/)
