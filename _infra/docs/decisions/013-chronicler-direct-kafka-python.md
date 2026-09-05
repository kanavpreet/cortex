# Chronicler Kafka Consumption: Direct `confluent-kafka-python`

Date: 2026-05-12

Status: `proposed`

Collaborators: @sumit_chachadi

Supersedes: [016-chronicler-kafka-consumer.md](016-chronicler-kafka-consumer.md)

## Context

[ADR 016](016-chronicler-kafka-consumer.md) chose an "Omnes JVM sidecar" to bridge Kafka to a Chronicler FastAPI endpoint. On implementation review the premise turned out to be wrong:

- `omnes-consumer-agent` (the only Omnes-related kube-gen component) is a **pull-based** JVM sidecar exposing RPC port 19090 for an **in-process** Omnes client library (Java/Ruby). It does not POST HTTP and there is no `callback-url`/`forwarding-url` field in its schema.
- The official internal Kafka how-to ([realtime-data-docs/kafka/how#python](https://developers.a.musta.ch/docs/default/component/realtime-data-docs/kafka/how/#python)) explicitly says: *"For Consumer, you will have to read from a JVM process similar to the Ruby instructions above."* No paved-path Python consumer exists.
- The only **Python** service consuming `yoyo.callback.*` in production today (`airbnb/bento-box:apps/snake`) uses `confluent-kafka-python` connecting **directly to Kafka brokers** via AirMesh — no sidecar.

So Chronicler needs a different approach.

## Decision

Consume the three `yoyo.callback.matik_*_webhook_events` topics directly from Python using `confluent-kafka-python`, routed to the Kafka bus via AirMesh. Drop FastAPI; Chronicler runs as a long-running consumer loop.

- No sidecar.
- **Chronicler is the HMAC trust boundary.** The initial framing assumed Yoyo validated signatures upstream; on implementation that turned out to be wrong — Yoyo passes inbound webhook bodies through to Kafka unverified. Each `ProviderTransformer.validate_signature` (GHE `X-Hub-Signature-256`, Incident.io Svix) is therefore enforced in the consumer loop before any downstream publish. Jira DC has no signing scheme; mitigation is documented in [ADR 020](020-jira-webhook-spoofing-mitigations.md).
- The `ProviderTransformer` registry built for the FastAPI implementation is reused unchanged.
- Offsets are committed only after both the Scribe and Enricher publishes succeed (at-least-once; Scribe is upsert-safe).

## Consequences

- Off the documented paved path. Mitigated by the `bento-box/snake` precedent and a heads-up to `#data-infra-support` before staging deploy.
- Chronicler no longer exposes an HTTP service. Liveness/readiness use exec probes (`kill -0 1`).
- Adding a new provider (JIRA, Incident.io) requires a new transformer + adding the topic to the consumer config — no Thrift IDL, no sidecar tweak.
- Kafka ACLs must be requested via `#data-infra-support` for the consumer group on each `yoyo.callback.matik_*` topic before each environment can read.
- Kafka cluster (`kafka-prod-a` vs `kafka-main`) still needs confirmation from `#api-infra` per environment.
- On-wire payload encoding has been confirmed as **two-layer Thrift binary** (`TBinaryProtocol`): jitney `RawMessage` envelope wrapping a Yoyo `CallbackEvent` struct. The deserializer is implemented in-tree as a pure-Python reader in `matik/chronicler/models/_thrift_binary.py` rather than via `airbnb-jitney-schemas` Python bindings — the field set we touch (i32/i64, string, `map<string,string>`) is small enough that avoiding a codegen step is the better trade.
