# See https://git.musta.ch/airbnb/jorb-images to know what the
# This image was built with this dockerfile as reference: https://git.musta.ch/airbnb/twig/blob/e6d8d493d25a78c896cbf12ca62f79259ce669fc/_infra/container-images/base-ci.dockerfile
FROM 172631448019.dkr.ecr.us-east-1.amazonaws.com/ubuntu2204:2026-08-07-daily

# Install base dependencies
# Note: cmake, pkg-config, libssl-dev are required for rugged gem (cloudcov dependency)
RUN apt-get install -y \
      git \
      ca-certificates \
      curl \
      cmake \
      pkg-config \
      libssl-dev

# Needed ruby for cloudcov
ARG RUBY_VERSION=3.1
RUN /jorb/scripts/install-ruby.sh ${RUBY_VERSION}

# cloudcov changelog https://git.musta.ch/airbnb/cloudcov/blob/master/CHANGELOG.md
ARG CLOUDCOV_VERSION=3.2.1
ARG TITANIC_VERSION=1.4.0
RUN gem install titanic:${TITANIC_VERSION} \
                cloudcov:${CLOUDCOV_VERSION}

# Install Python
ARG PYTHON_VERSION=3.13
RUN /jorb/scripts/install-python.sh ${PYTHON_VERSION} --system-default

# Install uv package manager and pre-commit
RUN pip install uv pre-commit

# Set working directory
WORKDIR /workspace
