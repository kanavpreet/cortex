# Production Dockerfile for Enigmatologist Service
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

# Install dependencies with enigmatologist extras only
RUN uv sync --frozen --no-install-project --no-dev --extra enigmatologist

# Copy source code
COPY matik/ .

# Install the project
RUN uv sync --frozen --no-dev --extra enigmatologist


# Runtime stage - minimal image
FROM managed-images.musta.ch/ubuntu2204 AS runtime

# Install Python using company script
ARG PYTHON_VERSION=3.13
RUN /jorb/scripts/install-python.sh --system-default ${PYTHON_VERSION}

# Install runtime dependencies
RUN apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -g 1000 matik && \
    useradd -r -u 1000 -g matik matik

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder --chown=matik:matik /build/.venv /app/.venv

# Copy only the service and shared code
COPY --chown=matik:matik matik/enigmatologist /app/enigmatologist
COPY --chown=matik:matik matik/common /app/common

# Set PATH to use the virtual environment
ENV PATH="/app/.venv/bin:$PATH"

# Change ownership to non-root user
RUN chown -R matik:matik /app

# Switch to non-root user
USER matik

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD pgrep -f enigmatologist || exit 1

# Run the application with dumb-init for proper signal handling
ENTRYPOINT ["dumb-init", "--"]
CMD ["python", "-m", "enigmatologist"]
