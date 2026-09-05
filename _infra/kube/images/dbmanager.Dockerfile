# Production Dockerfile for Migrator Service
# Multi-stage build for optimal image size and security

# Build stage - install dependencies with uv
FROM managed-images.musta.ch/ubuntu2204 AS builder

# Install Python using company script
ARG PYTHON_VERSION=3.13
RUN /jorb/scripts/install-python.sh --system-default ${PYTHON_VERSION}

# Install uv (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

# Set uv environment variables for reproducible builds
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /build

# Copy dependency files first for better layer caching
COPY matik/pyproject.toml matik/uv.lock ./

# Install dependencies with migrator extras only
RUN uv sync --frozen --no-install-project --no-dev --extra migrator

# Copy source code
COPY matik/ .

# Install the project
RUN uv sync --frozen --no-dev --extra migrator


# Runtime stage - minimal image
FROM managed-images.musta.ch/ubuntu2204 AS runtime

# Install Python using company script
ARG PYTHON_VERSION=3.13
RUN /jorb/scripts/install-python.sh --system-default ${PYTHON_VERSION}

# Install runtime dependencies
RUN apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    curl \
    php-cli \
    php-mysql \
    && rm -rf /var/lib/apt/lists/*

# Download AWS RDS CA bundle for secure RDS connections
RUN curl -o /usr/local/share/ca-certificates/rds-combined-ca-bundle.crt https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem && \
    update-ca-certificates

# Create non-root user for security
RUN groupadd -g 1000 matik && \
    useradd -r -u 1000 -g matik matik

WORKDIR /app

# Download Adminer for web-based DB management
RUN curl -Lo /app/adminer.php https://www.adminer.org/latest.php

# Wrapper that pre-fills the server field from ADMINER_DEFAULT_SERVER env var
RUN printf '<?php\nif (empty($_GET["server"])) { $_GET["server"] = getenv("ADMINER_DEFAULT_SERVER"); }\ninclude __DIR__ . "/adminer.php";\n' > /app/index.php

# Adminer uses the replica for read-only browsing; migrations use the master via config
ENV ADMINER_DEFAULT_SERVER=biztech-replica.proxysql-production:3306

# Copy the virtual environment from builder
COPY --from=builder --chown=matik:matik /build/.venv /app/.venv

# Copy only the service and shared code
COPY --chown=matik:matik matik/migrator /app/migrator
COPY --chown=matik:matik matik/common /app/common

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Change ownership to non-root user
RUN chown -R matik:matik /app

# Switch to non-root user
USER matik

# Start Adminer on port 8080 and keep the container alive for exec access
ENTRYPOINT ["dumb-init", "--"]
CMD ["sh", "-c", "php -S 0.0.0.0:8080 -t /app /app/index.php & sleep infinity"]
