# CI/CD Pipeline

This document explains how Continuous Integration (CI) and Continuous Deployment (CD) works for the Matik platform.

## Overview

Matik uses a multi-stage CI/CD pipeline powered by:

- **CI**: [Jorb](https://air.bb/jorb) and [Buildkite](https://git.musta.ch/airbnb/jorb_dispatcher_buildkite) for automated testing and builds
- **CD**: [Spinnaker](https://developers.airbnb.tools/projects/spinnaker/docs/users/managing_pipelines) for deployment orchestration
- **Deployboard**: OneTouch for configuration and deployment management

## Continuous Integration (CI)

The CI pipeline is defined in `_infra/ci/` and runs automatically on every pull request. All CI jobs must pass before code can be merged.

### CI Jobs

The pipeline runs four required jobs in parallel:

#### 1. Format Check (`_infra/ci/jobs/format.yml`)

- Runs `go fmt ./...` to verify code formatting
- Ensures all Go code follows standard formatting conventions
- **On failure**: Shows which files need formatting

#### 2. Build (`_infra/ci/jobs/build.yml`)

- Validates `go.mod`, `go.sum`, and `vendor/` are up-to-date
- Builds all four service binaries:
  - `historian` - Catalogs past events
  - `chronicler` - Catalogs real-time events via webhooks
  - `catalog` - API server
  - `correlator` - Correlates events together
- Verifies all binaries are executable
- **On failure**: Provides troubleshooting steps

#### 3. Test (`_infra/ci/jobs/test.yml`)

- Runs all tests with `go test -v -race -coverprofile=coverage.out -covermode=atomic ./...`
- Includes race condition detection
- Generates code coverage reports
- Processes coverage with [CloudCov](https://developers.airbnb.tools/docs/infra/cloudcov)
- **On failure**: Shows detailed test output

#### 4. Lint (`_infra/ci/jobs/lint.yml`)

- Runs `golangci-lint run ./...` for code quality checks
- Enforces Go best practices and catches potential bugs
- **On failure**: Shows linting issues found

#### 5. Multi-Arch Docker Build

- Builds multi-architecture Docker images
- Pushes to ECR (Elastic Container Registry)
- Uses the OneTouch build system

### CI Optimizations

- **Caching**: Go modules are cached based on `go.sum` checksum to speed up builds
- **Parallel Execution**: All jobs run in parallel for faster feedback
- **Banners**: Visual success/failure banners provide quick status updates
- **Coverage Tracking**: Code coverage trends are tracked across branches

### CI Configuration Files

- `_infra/ci/dispatch.yml` - Main CI configuration defining all jobs
- `_infra/ci/cloudcov.yml` - Enables code coverage reporting
- `_infra/ci/Dockerfile` - Docker image used for CI jobs

## Continuous Deployment (CD)

This section provides instructions for deploying the Matik application across various environments, including sandbox, staging, and production.

**Quick Link:** [Matik Spinnaker CD Pipelines](https://spinnaker.a.musta.ch/#/applications/matik/executions?pipeline=deploy%20to%20sandbox)

---

## Spinnaker Overview

The CD pipeline for Matik is managed through [Spinnaker](https://developers.a.musta.ch/docs/default/component/spinnaker/configuration/pipelines?utm_source=glean), which automates the deployment process to ensure smooth rollouts of new application versions.

### Pipeline Configuration

Pipeline definitions are located in the **`_infra/cd/pipelines/`** directory and are written in YAML format.

---

## Available Pipelines

### 1. Default Pipeline (`default.yml`)

- **Purpose:** Main pipeline for deploying to Staging and Production environments
- **Trigger:** Can only be triggered from the `main` branch
- **Environments:** Staging → Production

### 2. Sandbox Sandbox (`deploy_to_sandbox.yml`)

- **Purpose:** Deploy to sandbox environment for testing
- **Trigger:** Can be triggered from any feature branch
- **Environment:** Sandbox

### 3. Staging Deployment (`deploy_to_staging.yml`)

- **Purpose:** Deploy to Staging environment for pre-production validation
- **Trigger:** Can only be triggered from the `main` branch
- **Environment:** Staging

### 4. Emergency Pipeline (`emergency.yml`)

- **Purpose:** Fast-track hotfixes to Production
- **Trigger:** Can be triggered from any branch
- **Environment:** Production
- **Use Case:** Critical bug fixes and urgent patches

---

## Additional Resources

- **Pipeline Code:** [`_infra/cd/`](../../cd/) directory
- **Spinnaker Documentation:** [Airbnb Developer Portal](https://developers.a.musta.ch/docs/default/component/spinnaker/configuration/pipelines?utm_source=glean)
