# Multi-stage Dockerfile for Matik services (Python with uv)
# Best practices: minimal final image, non-root user, multi-stage build

# Build stage - install dependencies with uv
FROM python:3.13-slim AS builder

# Install uv (pinned to match local development version)
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /uvx /bin/

# Set uv environment variables for reproducible builds
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /build

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock* ./

# Install dependencies (without the project itself)
# Include all service extras for local development
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev \
    --extra historian --extra chronicler --extra api --extra migrator --extra mcp_server --extra enigmatologist

# Copy source code
COPY . .

# Install the project with all extras
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
    --extra historian --extra chronicler --extra api --extra migrator --extra mcp_server --extra enigmatologist


# Final stage - minimal runtime image
FROM python:3.13-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gettext-base \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 matik && \
    useradd -u 1000 -g matik -m matik

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder --chown=matik:matik /build/.venv /app/.venv

# Copy application code (includes entrypoint.sh)
COPY --chown=matik:matik . /app/
RUN chmod +x /app/entrypoint.sh

# Create writable config directory for entrypoint
RUN mkdir -p /app/config && chown matik:matik /app/config

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER matik

# Health check (can be overridden per service in docker-compose)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD pgrep -f "python" || exit 1

# Set entrypoint to process configs and source env vars
ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["python", "main.py"]
