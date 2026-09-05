# Chronicler Kafka Consumer: Omnes JVM Sidecar vs. Jitney Consumer

Date: 2026-05-12

Status: `superseded by` [018](013-chronicler-direct-kafka-python.md)

> **Why superseded:** This ADR's premise — that `omnes-consumer-agent` is an HTTP-forwarding sidecar that POSTs Kafka messages to a colocated FastAPI service — is incorrect. The Omnes agent is a pull-based RPC sidecar designed for in-process Java/Ruby Omnes client libraries; it does not POST HTTP. The kube-gen component schema has no `callback-url` field. The official internal Kafka how-to (`https://developers.a.musta.ch/docs/default/component/realtime-data-docs/kafka/how/#python`) explicitly says Python consumers must read from a JVM process. See ADR 018 for the corrected approach (direct `confluent-kafka-python` against the Kafka bus via AirMesh, following the `bento-box/snake` precedent).

Collaborators: @sumit_chachadi

## Context

The Chronicler needs to consume webhook events from Yoyo (`callbacks.airbnb.com`), which publishes them onto Kafka topics (e.g. `yoyo.callback.matik_github_webhook_events`). There are two established patterns at Airbnb for consuming Kafka in Python services:

**Option A — Omnes JVM sidecar (HTTP)**
A JVM container runs alongside the Python service. It consumes from Kafka and forwards each message to the Python service via HTTP POST on localhost. The Python service sees a plain JSON envelope (`com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.x`) and responds with an HTTP status code to signal success or retry.

**Option B — Jitney consumer (Thrift)**
Used in the `twig` Python monorepo (e.g. Argus). A JVM `MasterProcess`/`WorkerProcess`/`ProcessSupervisor` sidecar bridges Kafka to a Python `BaseConsumer` via Thrift RPC (`TBinaryProtocol`, port 19090+). The Python service must generate Thrift stubs from `.thrift` IDL files and depend on the `thrift` runtime library.

## Decision

Use the **Omnes JVM sidecar** approach.

The Chronicler is an event volume of ~tens-to-hundreds of webhooks per day — nowhere near the throughput where Thrift's serialization overhead becomes meaningful. At that scale:

- HTTP/JSON is debuggable with `curl` and standard FastAPI tooling; Thrift RPC is not.
- No Thrift dependency, no `.thrift` IDL files, no codegen step in the build pipeline.
- The FastAPI callback route is ~150 lines of standard Python async code with no Thrift-specific abstractions.
- Retry semantics are simple: return 200 to ack, 500 to signal the sidecar should retry.
- Omnes is the pattern Yoyo's own ADR 016 prescribes for consumers of its callback events.

## Consequences

- Chronicler's Kafka subscription is declared in the kube-gen `omnes-consumer-pool:` block; no separate consumer deployment is needed.
- Adding a new provider topic (JIRA, Incident.io) requires adding one line to the subscription list and one transformer in code — no Thrift IDL changes.
- The Chronicler cannot consume arbitrary Kafka topics that are not Yoyo callback events without revisiting this decision.
