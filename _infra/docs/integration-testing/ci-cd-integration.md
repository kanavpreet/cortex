# UAT CI/CD Integration

## Overview

UAT tests run automatically as a [**Pokey integration test stage**](https://developers.a.musta.ch/docs/default/component/pokey/) in the Spinnaker CD pipeline after every staging deployment. They are **not triggered on PRs** — they run as a post-deploy gate.

Here is the reference on how to test [Python code](https://developers.a.musta.ch/docs/default/component/pokey/writing-integration-tests/testing-a-python-service-with-pytest/).

```mermaid
graph LR
    subgraph Dev["Development"]
        PR[GitHub PR]
    end

    subgraph CI["Buildkite CI"]
        PR -->|push| BK[ci_required jobs<br/>format, lint, test, migrations]
        PR -->|push| BKO[ci_optional jobs<br/>scripts-test, scripts-lint, scripts-format]
    end

    subgraph CD["Spinnaker CD Pipeline"]
        BK -->|merge to main| SP1[Deploy to Sandbox]
        SP1 --> SP2[Deploy to Staging]
        SP2 --> UAT[Pokey Integration Tests<br/>matik-uat job]
        UAT -->|pass| MANUAL[Manual Judgment Gate]
        MANUAL -->|approve| SP3[Deploy to Canary]
        SP3 --> SP4[Deploy to Production]
        UAT -->|fail| BLOCK[Block promotion]
    end
```

---

## Pokey Integration

Matik uses [**Pokey**](https://developers.a.musta.ch/docs/default/component/pokey/), Airbnb's integration testing platform, to run UAT as part of the CD pipeline. Pokey runs tests in an isolated Kubernetes pod inside the target environment's cluster. It can support both sycronous testing and cronjob-style testing. [Reference](https://developers.a.musta.ch/docs/default/component/pokey/monitoring-your-service-with-integration-tests/#setting-up-a-cron-integration-test-job).

### How Pokey Works

```mermaid
sequenceDiagram
    participant SP as Spinnaker
    participant PK as Pokey
    participant POD as Test Pod (integration-tests/pytest)
    participant K8s as Staging K8s

    SP->>PK: integrationTests stage triggered
    PK->>POD: Deploy test pod (requirements.txt installed)
    POD->>K8s: Resolve SERVICE_HOSTNAME_matik_api env var
    POD->>K8s: HTTP contract tests (api tests)
    POD->>K8s: LLM connectivity tests (llm tests)
    K8s-->>POD: results
    POD-->>PK: pytest exit code + HTML report
    PK-->>SP: Pass / Fail
```

### Pokey Configuration (`_infra/integration-tests.yml`)

Currently only implementing syncronous testing.

```yaml
jobs:
  - name: matik-integration-tests
    framework: pytest
    airmeshOnly: true
    timeout: 600
```

Pokey injects `SERVICE_HOSTNAME_<service>` and `SERVICE_PORT_<service>` environment variables into the test pod. The `conftest.py` reads these automatically — no configuration needed in the CD pipeline.

### Spinnaker Stage (`_infra/cd/pipelines/deploy_to_staging.yml`)

```yaml
- name: integration-tests
  type: integrationTests
  parameters:
    environment: staging
  timeoutMinutes: 15
  title: Run UAT integration tests
  dependsOn:
    - deploy-to-staging
```

---

## Why Not CI (PR)?

UAT tests require:
- Live network access to deployed service endpoints
- Running inside the cluster to reach cluster-internal services

These constraints make UAT unsuitable for standard PR CI jobs. Running them post-deploy in Pokey, where the test pod lives inside the cluster, is the correct execution model.

---

## Running Locally (for debugging)

Engineers can run UAT locally against any environment using the AirMesh devAccess URLs and an IAP token. View the [getting started](getting-started.md) for detailed instructions.


---

## Failure Handling

When UAT fails:

1. The Spinnaker pipeline blocks at the `integration-tests` stage
2. Review the Pokey test output and HTML report attached to the pipeline execution
3. Fix the issue and re-deploy to staging to trigger UAT again
4. Engineers can also run locally to iterate faster (see above)
