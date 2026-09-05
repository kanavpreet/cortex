# Chronicler Service Development Guide

> Supersedes the FastAPI/Omnes-sidecar version of this doc. See [ADR 016](../decisions/016-chronicler-kafka-consumer.md) (superseded) and [ADR 018](../decisions/013-chronicler-direct-kafka-python.md) for the architecture history.

## Overview

The Chronicler is a stateless Kafka consumer that subscribes to Yoyo callback topics, transforms each webhook event, and publishes to two SQS queues:

1. **Scribe high-priority queue** — sanitized base message for DB persistence.
2. **Enricher queue** — LLM-enrichment request carrying the sensitive fields.

The Chronicler does **not** write to the database. Scribe owns DB writes; the Enricher owns LLM summarization.

### Technology Stack

- **Python 3.13+**
- **`confluent-kafka-python`** for direct Kafka consumption via AirMesh (following the `bento-box/snake` precedent — there is no off-the-shelf Omnes HTTP-forwarding sidecar for Python at Airbnb).
- **In-tree Thrift binary decoder** ([chronicler/models/_thrift_binary.py](matik/chronicler/models/_thrift_binary.py)) — pure-Python `TBinaryProtocol` reader; no dependency on the `thrift` package and no generated jitney bindings in the build.
- **asyncio** for the consumer loop and concurrent SQS publishing.
- **Pydantic** for message validation.
- **boto3** (via `SQSPublisher` and `EnrichmentPublisher`).
- **OpenTelemetry / Telescope** for metrics.
- Location: `matik/chronicler/`.

### Design Principles

1. **Stateless** — no database dependency.
2. **Chronicler is the HMAC trust boundary** — Yoyo passes webhook payloads through to Kafka unverified, so each provider transformer's `validate_signature` runs in the consumer before any downstream publish (GHE: `X-Hub-Signature-256`; Incident.io: Svix; Matik/generic: `X-Matik-Signature-256`; Jira: no signing scheme — see [ADR 020](../decisions/020-jira-webhook-spoofing-mitigations.md) for the URL-secret + sanity-filter mitigation).
3. **Sensitive data isolation** — fields destined for LLM enrichment (PR body, JIRA description) are stripped from the base message; they only flow through the Enricher.
4. **Modular providers** — a `ProviderTransformer` registry lets new webhook sources land with just a transformer module and a Kafka topic in the consumer config.
5. **At-least-once delivery** — Kafka offsets commit only after both SQS publishes succeed. Scribe is upsert-safe, so duplicate base messages on retry produce the same DB state.

## Ingestion Pipeline

```
Webhook Provider (GHE / JIRA / Incident.io / OpsBot via the generic Matik webhook)
        │
        ▼ HTTP POST
   Yoyo (callbacks.airbnb.com)
        │
        ▼ Kafka produce
   yoyo.callback.matik_<provider>_webhook_events  (5 topics, kafka-prod-a)
        │
        ▼ confluent-kafka-python via AirMesh
   Chronicler (Python pod, long-running consumer loop)
        │
        ├─▶ Scribe HP SQS Queue (sanitized base message)
        │         └─▶ Scribe → DB UPSERT
        │
        └─▶ Enricher SQS Queue (enrichment request)
                  └─▶ Enricher → LLM → Scribe LLM Queue → DB UPDATE
```

## CallbackEvent envelope

Each Kafka record is a **two-layer Thrift binary** struct (`TBinaryProtocol`, big-endian) produced by Yoyo:

```
Kafka record bytes
  └─ jitney RawMessage (field 2 = raw_event: binary)
        └─ Yoyo CallbackEvent (com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.x)
```

Decoding is implemented from scratch in [chronicler/models/_thrift_binary.py](matik/chronicler/models/_thrift_binary.py) — a ~170-line pure-Python `TBinaryProtocol` reader. We deliberately do **not** depend on the `thrift` package or pre-generated `airbnb-jitney-schemas` bindings: the field set we need is small, and avoiding codegen keeps the build pipeline simple. Field IDs are pinned as constants in [chronicler/models/callback_event.py](matik/chronicler/models/callback_event.py); a jitney schema bump that re-orders fields will surface as a `CallbackEventDeserializationError`, not a silent misread.

The `CallbackEvent` fields used by Chronicler:

| Field | Thrift id / type | Notes |
|---|---|---|
| `external_service_type` | 1 / string | e.g. `matik_sandbox_ghe_webhook_events`. Mapped to a short provider name via the `CallbackEvent.provider` property (tokenizes on `_` and matches `github`/`ghe`/`jira`/`incidentio`/`generic`) so per-env prefixes route uniformly. |
| `host` | 3 / string | Yoyo-side hostname the webhook was received on (e.g. `callbacks.airbnb.com`). Captured for diagnostics. |
| `path` | 4 / string | Yoyo-side URL path. Captured for diagnostics; **not** parsed for Layer-1 URL-secret validation (the secret is terminated at Yoyo). |
| `headers` | 6 / `map<string,string>` | Original HTTP headers, **lowercased** by Yoyo. Look up case-insensitively. |
| `payload` | 7 / binary | Raw webhook body bytes that the provider POSTed to Yoyo — UTF-8 JSON for all three providers today. HMAC is computed over `payload.encode("utf-8")` (these exact bytes), then `json.loads` is applied to obtain the dict passed to the transformer. |
| `request_received_ts` | 11 / i64 | Unix ms when Yoyo received the webhook. |
| `schema` | 31337 / string | Jitney schema identifier (e.g. `com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4`). |

Non-`map<string,string>` map kinds, structs, lists, and sets we don't read are type-skipped by `_Reader.skip_value`.

## File Structure

```
matik/chronicler/
├── __init__.py
├── __main__.py                    # python -m chronicler entry point
├── main.py                        # config load, publishers wiring, consumer loop bootstrap
├── consumer.py                    # KafkaConsumerLoop + per-message processing
├── models/
│   ├── __init__.py
│   └── callback_event.py          # CallbackEvent + deserialize_record_value
├── transformers/
│   ├── __init__.py                # Imports each provider module to trigger auto-registration
│   ├── base.py                    # ProviderTransformer protocol + registry
│   ├── github_pr.py               # GitHub PR transformer (auto-registers as "github")
│   ├── incidentio.py              # Incident.io transformer (auto-registers as "incidentio")
│   ├── jira.py                    # Jira webhook transformer (auto-registers as "jira")
│   └── matik_webhook.py           # Generic Matik webhook transformer (auto-registers as "matik")
└── tests/
    ├── callback_event_test.py
    ├── github_pr_transformer_test.py
    ├── incidentio_transformer_test.py
    ├── jira_transformer_test.py
    ├── matik_webhook_transformer_test.py
    └── kafka_consumer_test.py
```

## Registered transformers

| Registry key | Class | Provider | Scope |
|---|---|---|---|
| `github` | `GitHubPRTransformer` | GitHub Enterprise pull-request webhooks | `event=pull_request`, `action=closed`, `merged=True` |
| `jira` | `JiraTransformer` | Atlassian Jira webhooks | `webhookEvent` ∈ `{jira:issue_created, jira:issue_updated, comment_created, comment_updated}`; issue key starts with `TCMR-`. `jira:issue_deleted` / `comment_deleted` are dropped because Scribe has no deletion path yet. |
| `incidentio` | `IncidentIOTransformer` | Incident.io public-incident webhooks (Svix-signed) | `event_type` ∈ `{public_incident.incident_created_v2, public_incident.incident_updated_v2}`. Other `public_incident.*_v2` events (status_changed, closed, etc.) are dropped; the historian's crawl backstops terminal-state updates. |
| `matik` | `MatikWebhookTransformer` | Generic Matik-originated webhook (e.g. OpsBot's incident-channel-summary feed) — a webhook *we* define the contract for, not a vendor's | `payload["category"]` ∈ `{incident_channel_summary}`. Unlike the vendor providers above, this one is deliberately generic: new sub-use-cases add a `category` value and a Scribe `source_type` rather than a new provider registration, transformer, topic, or secret. See [ADR 023](../decisions/023-generic-matik-webhook.md). |

## Provider Transformer Protocol

Each provider implements:

```python
class ProviderTransformer(Protocol):
    source_type: str               # e.g. "ghe_pr" — matches EnrichmentRequest.source_type
    event_type_header: str         # e.g. "X-GitHub-Event"; "" if event lives in the payload body
    webhook_secret_key: str        # attribute name on ChroniclerConfig that holds the HMAC secret; "" disables HMAC
    def validate_signature(self, body: bytes, headers: dict, secret: str) -> bool: ...
    def should_process(self, event_type: str, payload: dict) -> bool: ...
    def to_base_message(self, payload: dict) -> BaseModel: ...
    def to_enrichment_request(self, payload: dict, task_id: str) -> EnrichmentRequest | None: ...
```

Transformers register themselves at import time:

```python
register_transformer("github", GitHubPRTransformer())
register_transformer("jira", JiraTransformer())
```

### HMAC signature handling per provider

Chronicler is the trust boundary for inbound webhooks — Yoyo passes payloads through unverified. Each transformer decides its own signature scheme via `validate_signature`:

| Provider | Scheme | Implementation |
|---|---|---|
| GitHub | HMAC-SHA256 over the raw body, compared to `X-Hub-Signature-256` (case-insensitive) | `GitHubPRTransformer.validate_signature`; secret in `ChroniclerConfig.github_webhook_secret` (secret-lair key `chronicler.github_webhook_secret`) |
| Jira | **None** — Atlassian Jira webhooks have no signing scheme and the webhook UI exposes no shared-secret / custom-header field. `webhook_secret_key = ""` short-circuits the consumer's HMAC check. | `validate_signature` is a defensive stub that returns `False`; it should never be called. Spoofing is mitigated by [ADR 020](../decisions/020-jira-webhook-spoofing-mitigations.md): a secret-bearing Yoyo callback URL (Layer 1) plus transformer-level sanity filters in `JiraTransformer.should_process` — Airbnb-email author, matching `issue.self` host, strict `project.key == "TCMR"`, and a 24 h `timestamp` window (Layer 2). Network-ingress restriction is not available because Jira DC sits on BizTech outside the AirMesh trust zone. |
| Incident.io | Svix endpoint signature (HMAC-SHA256 with replay-window protection). Headers `webhook-id` / `webhook-timestamp` / `webhook-signature` (or the legacy `svix-*` aliases) are verified by the official `svix` Python SDK against the `whsec_…` endpoint secret. | `IncidentIOTransformer.validate_signature` delegates to `svix.webhooks.Webhook.verify`; secret in `ChroniclerConfig.incidentio_webhook_secret` (secret-lair key `chronicler.incidentio_webhook_secret`). |
| Matik/generic | HMAC-SHA256 over the raw body, compared to `X-Matik-Signature-256` (case-insensitive) — same construction as GitHub's, but for a webhook Matik originates itself rather than inheriting a vendor's convention. | `MatikWebhookTransformer.validate_signature`; secret in `ChroniclerConfig.generic_webhook_secret` (secret-lair key `chronicler.generic_webhook_secret`). |

When a provider's `webhook_secret_key` resolves to `None` or `""` at runtime, the consumer logs a one-time startup warning (for GHE) and skips per-event validation. This is acceptable for sandbox bootstrapping but staging/prod must populate the secret via secret-lair before going live.

## Onboarding a new webhook provider

A new webhook source touches three sides — the provider itself, Yoyo, and the Matik repo — and ships across roughly the order below. Steps 1–4 are infra coordination that runs in parallel with the code changes in steps 5–9.

### 1. Provider-side webhook configuration

- Confirm what the provider can emit: event types, delivery transport (HTTP push only — anything Matik consumes flows via Yoyo), and **whether it signs payloads**. If it does, capture the exact scheme (HMAC algorithm, header name, body framing, replay-protection details) — that's what the transformer's `validate_signature` will reimplement.
- If the provider has no signing scheme (the Jira case), write up the spoofing-mitigation plan in a new ADR before going live — `decisions/020-jira-webhook-spoofing-mitigations.md` is the template. At minimum: a secret-bearing callback URL plus transformer-side payload sanity checks. Do not ship an unsigned, un-mitigated provider.
- Have the provider's admin/owner ready to point its outbound webhook at the Yoyo URL once Yoyo finishes provisioning in step 2.

### 2. Yoyo callback registration (`#api-infra`)

- File a ticket with `#api-infra` to register a new `external_service_type` (e.g. `matik_<provider>_webhook_events`) and the matching public callback URL on `callbacks.airbnb.com`. Yoyo provisions one Kafka topic per `external_service_type`, named `yoyo.callback.<external_service_type>`.
- Once `#api-infra` confirms provisioning, verify the topic exists via Kafka Manager (`kafka-prod-a` cluster) before moving on to step 3 (ACLs).
- Provide them with:
  - the desired `external_service_type` (use the existing `matik_<env>?_<provider>_webhook_events` naming convention so the `CallbackEvent.provider` tokenizer keeps working — see step 5),
  - the public URL fragment, **including** any unguessable secret path segment if you're using URL-secret as a mitigation layer,
  - the target environment(s).
- Yoyo does **not** validate provider signatures before producing to Kafka. The HMAC trust boundary lives in Chronicler (step 6).
- Precedent: the generic Matik webhook (`matik` provider, token `generic`) only registered two `external_service_type`s — `matik_generic_webhook_events` (production) and `matik_sandbox_generic_webhook_events` (sandbox) — no separate staging type. Staging shares the production topic/type (see the tokenizer note in `CallbackEvent.provider`). Don't assume every provider gets three per-env registrations; confirm what `#api-infra` actually provisions and adjust the topics list accordingly.

### 3. Kafka ACLs (`#data-infra-support`)

For every environment, request **consumer-group read ACLs** on the newly provisioned `yoyo.callback.matik_<provider>_webhook_events` topic for our consumer group (`Env.Params.chronicler.kafka.group_id`). Without this the consumer will subscribe but get zero records and no useful error.

### 4. Secret-lair entry (only if the provider signs)

Add the shared secret under a deterministic key:

```
chronicler.<provider>_webhook_secret      # e.g. chronicler.foo_webhook_secret
```

Provision it per environment via secret-lair, mount it through `_infra/secret-lair/*.json`, and wire it into [`matik-chronicler-config.yml`](../../kube/files/matik-chronicler-config.yml) as a templated `{{ .App.Secrets.chronicler.<provider>_webhook_secret | default "" }}`. **Do not** check secrets into git, and do not put env-specific HMAC values into kube-gen params.

### 5. Teach `CallbackEvent.provider` about the new provider

Open [chronicler/models/callback_event.py](matik/chronicler/models/callback_event.py) and add a `(token, short_name)` row to the `token_to_provider` list inside `CallbackEvent.provider`. The tokenizer splits `external_service_type` on `_` and matches against tokens, so the row only needs the unambiguous identifier (e.g. `("foo", "foo")`). Order matters when an existing token is a prefix of yours — put the more specific entry first.

### 6. Add a `ChroniclerConfig` field for the secret

In [common/models/chronicler_config.py](matik/common/models/chronicler_config.py), add a nullable field whose attribute name matches step 4's secret-lair key local part:

```python
foo_webhook_secret: str | None = Field(
    default=None,
    description="HMAC-SHA256 secret used to validate the X-Foo-Signature header on Foo webhooks.",
)
```

The transformer's `webhook_secret_key` (step 7) is exactly this attribute name — the consumer does `getattr(chronicler_config, transformer.webhook_secret_key)` at dispatch time. Skip this step for unsigned providers (and set `webhook_secret_key = ""` on the transformer).

### 7. Define the Scribe and Enricher message contracts

- Add a `FooBaseMessage` (Pydantic) to [common/models/scribe_messages.py](matik/common/models/scribe_messages.py) that captures the sanitized fields Scribe needs to upsert. Strip anything sensitive or free-text-heavy out of this model — those fields belong only in the enrichment request.
- If the new event type has any LLM-summarizable free text (PR body, ticket description, incident summary), define an `EnrichmentRequest.source_type` value for it. Otherwise `to_enrichment_request` returns `None`.

### 8. Write the transformer

Drop a new module under [matik/chronicler/transformers/](matik/chronicler/transformers/) implementing the `ProviderTransformer` protocol. Boilerplate sketch:

```python
class FooTransformer:
    source_type: str = "foo_event"
    event_type_header: str = "X-Foo-Event"          # "" if event lives in the body
    webhook_secret_key: str = "foo_webhook_secret"  # "" if provider does not sign

    def validate_signature(self, body, headers, secret):
        # Reimplement the provider's scheme exactly; compare with hmac.compare_digest.
        ...

    def should_process(self, event_type, payload):
        # Cheap structural filters first; expensive checks last.
        ...

    def to_base_message(self, payload):
        # Return the FooBaseMessage from step 7. STRIP sensitive free text here.
        ...

    def to_enrichment_request(self, payload, task_id):
        # Return None if nothing to enrich.
        ...

register_transformer("foo", FooTransformer())
```

Then import the module from [chronicler/transformers/__init__.py](matik/chronicler/transformers/__init__.py) so registration fires at startup. The fail-fast check in [`chronicler/main.py`](matik/chronicler/main.py) (`get_transformer(provider_name)` for the expected providers) is the safety net — add the new provider name to that check.

### 9. Wire up Kafka topics, deploy, verify

- Add the new topic to the env-specific `Env.Params.chronicler.kafka.topics` list (the `topics:` slot in [`matik-chronicler-config.yml`](../../kube/files/matik-chronicler-config.yml) is rendered from this list — do **not** hardcode topic names in YAML).
- Deploy chronicler to sandbox first. Trigger a sample webhook from the provider (or replay one via Yoyo's tooling) and confirm:
  - the topic shows up in Kafka Manager with a growing offset,
  - `matik_chronicler_messages_received_total{topic="…"}` increments,
  - `matik_chronicler_signature_validations_total{provider="foo",result="valid"}` increments,
  - Scribe and Enricher SQS messages are visible downstream,
  - `matik_chronicler_publish_outcome_total{target="scribe",status="success"}` and `target="enricher"` both go up.
- Add unit tests next to the new transformer module covering: a valid signed event, an event the filter should drop, an invalid signature, and (if applicable) the no-secret bootstrap path. Use the existing `kafka_consumer_test.py` fake-consumer pattern if you need an end-to-end test.
- Only after sandbox is green: push the secret-lair entry to staging, request ACLs in staging, deploy, repeat the verification, then prod.

## Configuration

See `_infra/kube/files/matik-chronicler-config.yml`. The `chronicler` section contains:

- `kafka.bootstrap_servers` — AirMesh host:port for the Kafka cluster.
- `kafka.group_id` — Kafka consumer group; suffix with environment.
- `kafka.topics` — list of `yoyo.callback.matik_*_webhook_events` topics.
- `kafka.auto_offset_reset` — defaults to `latest`.
- `scribe_queue_url` / `scribe_queue_region` — Scribe SQS queue.
- `enricher_queue_url` / `enricher_queue_region` — Enricher SQS queue (optional).

## Error semantics

| Condition | Behavior |
|---|---|
| Empty Kafka record | Commit and skip |
| Malformed Thrift envelope (`ThriftDecodeError` from either RawMessage or CallbackEvent decode, missing required field, or non-UTF-8 `payload`) → `CallbackEventDeserializationError` | Log + commit + skip; status `malformed_envelope` (poison-message dropped) |
| `CallbackEvent.provider` resolves to a name with no registered transformer | Log + commit + skip; status `unknown_provider` |
| Inner provider payload is not valid JSON | Log + commit + skip; status `malformed_payload` |
| Transformer filters event out | Commit + skip |
| Scribe SQS publish fails | Raise → offset not committed → next poll redelivers |
| Enricher SQS publish returns None | Raise → offset not committed → next poll redelivers (Scribe is upsert-safe on re-publish) |

## Metrics

Chronicler emits OpenTelemetry metrics through the shared `TelescopeClient` to the local otel-collector sidecar. All instruments use the `matik_chronicler_` prefix and the `service="chronicler"` label. The implementation lives in [common/metrics/chronicler_metrics.py](matik/common/metrics/chronicler_metrics.py); the consumer loop in [chronicler/consumer.py](matik/chronicler/consumer.py) calls into it on every poll, signature check, transform decision, and SQS publish. Scribe SQS publishes are also instrumented via the existing `SQSPublisherMetrics` (`matik_sqs_*` series).

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `matik_chronicler_messages_received_total` | counter | `topic` | Non-empty Kafka polls |
| `matik_chronicler_messages_processed_total` | counter | `topic`, `provider`, `status` | `status` ∈ `success`, `filtered`, `signature_failed`, `unknown_provider`, `malformed_envelope`, `malformed_payload`, `error` |
| `matik_chronicler_message_processing_duration_seconds` | histogram | `topic`, `provider`, `status` | End-to-end wall-clock per Kafka message |
| `matik_chronicler_signature_validations_total` | counter | `provider`, `result` | `result` ∈ `valid`, `invalid`, `skipped_no_secret`, `skipped_no_scheme` |
| `matik_chronicler_publish_outcome_total` | counter | `target`, `provider`, `status` | `target` ∈ `scribe`, `enricher` |
| `matik_chronicler_kafka_poll_duration_seconds` | histogram | — | librdkafka `consumer.poll` latency |
| `matik_chronicler_kafka_errors_total` | counter | `error_type` | Kafka-level errors surfaced on consumed messages |

Suggested alert seeds (see the module docstring for the formal expressions):

- `rate(matik_chronicler_signature_validations_total{result="invalid"}[5m]) > 0` — possible spoofing or secret rotation drift.
- `rate(matik_chronicler_publish_outcome_total{status="failed"}[5m]) > 0.1` — downstream SQS health.
- `rate(matik_chronicler_messages_received_total[30m]) == 0` — consumer stalled or upstream silent.
- `rate(matik_chronicler_kafka_errors_total[5m]) > 0.1` — librdkafka errors flowing.

## Running locally

```bash
cd matik
uv sync --extra chronicler
uv run python -m chronicler
```

Set `MATIK_CONFIG_DIR` and provide a `matik-chronicler-config.yml` with valid Kafka and SQS settings (or point at LocalStack + a local Kafka).

## Testing

```bash
cd matik
uv run pytest chronicler/tests/ -v
```

`kafka_consumer_test.py` injects a fake Kafka consumer (MagicMock) into `KafkaConsumerLoop`, so tests don't require librdkafka or a running broker.
