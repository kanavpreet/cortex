# Reading Braintrust (BTQL) from a Kubernetes/AirMesh service

This guide explains how to get BTQL reads working from a plain Kubernetes/AirMesh workload (for example, a CronJob). It covers the two common blockers and how to fix them.

Write-side trace export works out of the box and does not need this setup. See [`common/llm_tracing/`](../../../matik/common/llm_tracing/).

Quick checklist:
- Add your caller to braintrust-proxy’s allowlist
- Send an auth header from Kubernetes by using ServiceIatCredential
- Add a server-side policy mapping to a default service account
- Deploy braintrust-proxy via its Spinnaker pipeline

## Symptoms

Calling BTQL from a k8s pod with:

- `genai_studio.braintrust.btql.client.BraintrustClient().btql_paginated(...)`
- or the wrapper in [`common/clients/braintrust_client.py`](../../../matik/common/clients/braintrust_client.py)

fails with one of:

```
HTTP 403: RBAC: access denied
```

```
HTTP 403: {"source":"braintrust-proxy","detail":"Permission Denied: No auth
header found and no service account or token configured in DataPlane.AIRBNB
data plane."}
```

These are two different gates. A plain AirMesh/Kubernetes workload fails both by default.

## Why it happens

1) braintrust-proxy has its own caller allowlist (mesh-level). If your service is not on it, requests are rejected with RBAC: access denied before auth is checked.

2) Even if allowlisted, the default client sends no auth header from Kubernetes. DefaultIatCredential only works from BigAir, Sandcastle, or an interactive session. BTQL is a non-logging endpoint and enforces per-project permissions, so unauthenticated requests are rejected.

## Fix 1: Add your caller to braintrust-proxy’s allowlist

braintrust-proxy keeps an allowlist separate from `dependentServices` in this repo’s [`_infra/mesh.yml`](../../mesh.yml). Without this, every call gets `RBAC: access denied`.

What to do:
- Add your AirMesh identity (`name.namespace`) to [`airbnb/twig:projects/genai-studio/service/braintrust-proxy/_infra/mesh.yml`](https://git.musta.ch/airbnb/twig/blob/master/projects/genai-studio/service/braintrust-proxy/_infra/mesh.yml) under `braintrust-proxy-production`’s `authz.allows`.
- Coordinate with `#genai-studio`. This is a shared, security-sensitive config.

Reference: [PR #16176](https://git.musta.ch/airbnb/twig/pull/16176) (added `matik-audit-root-cause-{sandbox,staging,production}`).

## Fix 2: Send a valid auth header from Kubernetes

Client side:
- Use ServiceIatCredential instead of the default. This mints an IAT using Hunter2s based on the pod’s AirMesh identity.

Example (see [`common/clients/braintrust_client.py`](../../../matik/common/clients/braintrust_client.py)):

```python
from airbnb_identity import ServiceIatCredential, current_context
from genai_studio.braintrust.proxy.api_client import init_httpx_client

self._btql = _GenaiBTQLClient()
if not current_context().is_interactive:
    self._btql.http = init_httpx_client(
        custom_credential=ServiceIatCredential(), timeout=30
    )
```

Also:
- Add `hunter2s-production.hunter2s-production` (or `hunter2s-staging...`) as a `dependentServices` entry for your service in [`_infra/mesh.yml`](../../mesh.yml). Hunter2s is a mesh service.

Server side:
- braintrust-proxy’s `ImpersonateOrServiceAccountPolicy` maps callers to service accounts only if they look like `svc-` or `svc_`. Plain AirMesh app names (e.g., `matik-audit-root-cause-sandbox`) do not.
- Add a `POLICY_BY_CALLER` entry that maps your caller’s namespaces to a `default_svc_account` in [`braintrust_proxy/policy/proxy_policy.py`](https://git.musta.ch/airbnb/twig/blob/master/projects/genai-studio/service/braintrust-proxy/braintrust_proxy/policy/proxy_policy.py) (the service-account name itself is defined in [`braintrust_proxy/config/braintrust_api_key.py`](https://git.musta.ch/airbnb/twig/blob/master/projects/genai-studio/service/braintrust-proxy/braintrust_proxy/config/braintrust_api_key.py)).

Notes:
- This is a code change in braintrust-proxy and requires `#genai-studio` review.
- Owning the target service account (e.g., `svc-matik`) is not sufficient; the policy mapping is separate.

Reference: [PR #16199](https://git.musta.ch/airbnb/twig/pull/16199) (added `SVC_MATIK` and `POLICY_BY_CALLER["matik-audit-root-cause"]`).

## Deploy the changes

Merging either PR is not enough. `braintrust-proxy` must be deployed manually (ask in `#genai-studio`) or wait for the daily scheduled deploy (PST morning).

## References

No developer-portal doc covers this; the fix came from a live investigation in `#genai-studio` and the two PRs above. Useful history:

- [Thread: BTQL 403s from `matik-audit-root-cause-sandbox`](https://airbnb.enterprise.slack.com/archives/C07FL8ERQUT/p1786479631829319?thread_ts=1786479487.483869&cid=C07FL8ERQUT) — the original bug report and the fix, with Haibo Ruan (genai-studio) confirming the root cause and PR #16199 resolving it.
- [Thread: the same braintrust-proxy 403 from an Airflow/Oneflow job](https://airbnb.enterprise.slack.com/archives/C07FL8ERQUT/p1780693957813779?thread_ts=1780602937.751179&cid=C07FL8ERQUT) — a different team hitting the identical auth error from a non-Kubernetes caller, worked around by explicitly passing `DefaultIatCredential()`.
