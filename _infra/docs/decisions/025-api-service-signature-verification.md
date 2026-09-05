# API Service-to-Service Signature Verification (Phase 1c)

Date: 2026-08-10

Status: `accepted`

Collaborators: @alfredo-moreira

Related: [Matik API & MCP Server — Access Posture: Path to Remediation](https://slate.airbnb.tools/9SGy4VzvyN/Matik+API+MCP+Server+Access+Posture:+Path+to+Remediation) (Phase 1c), [020-jira-webhook-spoofing-mitigations.md](020-jira-webhook-spoofing-mitigations.md)

## Context

Matik enforces no application-layer authentication today — the AirMesh service mesh (mTLS `allows` lists) is the only access control on `matik-api`. That control has a gap: mesh admission is a per-port decision. It decides which workload identities may open a connection to the service at all, but it does not distinguish between *routes* on that port, and nothing in `matik/api/` verified that a given request actually came from one of the specific workloads the mesh admitted (e.g. `matik-mcp-*`) rather than any other `allows`-listed identity sharing that port.

A deployed header-inspection spike (logging every header reaching the API from real traffic) found nothing mesh- or Kubernetes-injected that could serve as a caller-identity signal at the application layer. Mesh mTLS alone was judged insufficient on its own for this guarantee — see the access-posture doc's Phase 1c.

## Decision

Add a mesh-independent, application-layer proof of origin: shared-secret HMAC-SHA256 request signing, verified by the API on every request.

- **New module** `matik/common/utils/service_auth.py` mirrors the HMAC-SHA256 construction Chronicler already uses for inbound webhook verification, but adds a timestamp to the signed material — a webhook delivery is one-shot, this secures a live, repeatedly-called service, so a bare body signature would be replayable indefinitely.
- **Signed material:** `timestamp . method . path(+raw query string) . sha256(body)`, HMAC-SHA256 keyed by a shared secret (`ApiConfig.service_secret`, provisioned per environment via secret-lair, `api.service_secret`). Binding method and path means a signature captured for one route can't be replayed against another.
- **Headers:** `X-Matik-Service-Timestamp` and `X-Matik-Service-Signature: sha256=<hex digest>`.
- **Verification** (`verify_request`) fails closed: missing headers, a malformed timestamp, a timestamp more than 120s from server time in either direction, or a signature mismatch (compared with `hmac.compare_digest` to avoid timing leaks) all reject the request. The skew window bounds the replay window without requiring tightly synchronized clocks between services.
- **Enforcement point:** a `BaseHTTPMiddleware` dispatch (`api/main.py::create_service_signature_dispatch`), applied to the API globally — not scoped to `/v1/mcp/*` — since every enumerated caller (historian, enricher, enigmatologist, integration-tests, and MCP) signs its requests, not only MCP. Only `/health`, `/ready`, `/v1/mcp/health`, and `/` (kubelet probes, MCP's own preflight check of the API, and the welcome route — none of which return source data) are exempt.
- **Two independent, server-side config knobs**, both no-ops when unset:
  - `service_secret` unset/empty ⇒ the check is a total pass-through (e.g. local development without the secret configured).
  - `enforce_service_signature` (default `false`, i.e. shadow mode) ⇒ failures are logged (`info` in shadow mode, `warning` when enforced) and recorded to a `matik_api_service_signature_checks_total{result,reason,enforced}` counter, but a request is only rejected (401) once this is `true`.
- **Every caller recomputes signature headers per retry attempt**, not once before the retry loop — reusing headers built earlier would sign a timestamp that goes stale across backoff delays.
- `MatikApiClient` gains this automatically for every caller that already goes through it. Enigmatologist's `correlations/reliability/incident/nodes.py` fetch nodes call the API via raw `httpx.get()` instead of `MatikApiClient`, so signing was threaded through by hand there; consolidating onto `MatikApiClient` is tracked as a follow-up in the access-posture doc's open decisions, gated on `MatikApiClient.get_request()` gaining a configurable timeout.
- **Rollout:** shadow mode first in every environment. A standalone tester (`scripts/service_signature_tester/`) fires a fixed matrix of requests against a live deployment — valid signature, missing signature, wrong secret, stale timestamp, malformed timestamp, tampered body, tampered query, and each signature-exempt path — and renders a pass/fail report. `enforce_service_signature` was flipped to `true` in sandbox, staging, and production only after that tester reported every scenario matching its expected shadow-mode outcome.

## Consequences

- Every internal service that calls `matik-api` needs the shared secret provisioned (secret-lair `api.service_secret`) before enforcement is turned on for its environment, or its calls start failing with 401 once it is.
- Single shared secret, no key ID or rotation scheme — rotating it requires a coordinated redeploy of the API and every caller. Not addressed by this change.
- The 120s skew window bounds, but does not eliminate, replay: a captured valid request can be resent until its timestamp ages out.
- Because the check is applied globally rather than scoped to `/v1/mcp/*`, all of the API's data-serving routers (`incidentio`, `jira`, `ghe`, `correlations`, `enrichment`, `mcp`) gain this protection in the same change, not only the MCP-facing routes.
- This proves only that a caller possesses the shared secret — it carries no per-user identity and authorizes nothing at the record level. That is Phase 2 of the access-posture doc (IAT verification, LDAP-group-based source filtering, per-user audit logging) and is explicitly not part of this change.
- `matik_api_service_signature_checks_total` is the ongoing signal for whether shadow-mode logs are clean enough to enable enforcement in a given environment, and for alerting if enforced requests start failing afterward.

## Out of scope

- Phase 2 of the access-posture doc (per-user IAT verification, LDAP-group resolution, source-level authorization, audit logging) — a separate, not-yet-implemented phase.
- Mesh grant narrowing (Phase 1a) and the enumerated-caller list (Phase 1b) — mesh-config changes tracked and reviewed separately from this application-layer change.
- Secret rotation tooling and cadence.
