FROM 172631448019.dkr.ecr.us-east-1.amazonaws.com/ubuntu2204:2026-08-07-daily

# Install git for checking migration file changes
RUN apt-get install -y \
      git \
      ca-certificates \
      curl

# Install MySQL client for database operations
RUN /jorb/scripts/install-mysql.sh

# Install Python
ARG PYTHON_VERSION=3.13
RUN /jorb/scripts/install-python.sh ${PYTHON_VERSION} --system-default

# Install uv package manager
RUN pip install uv

# Set working directory for matik project
WORKDIR /workspace/matik

# Pre-install migrator dependencies for faster CI builds
# Actual project sync happens at CI runtime when repo is mounted
ENV UV_COMPILE_BYTECODE=1
