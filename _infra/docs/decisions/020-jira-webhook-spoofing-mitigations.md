# Jira Webhook Spoofing Mitigations (No HMAC Available)

Date: 2026-05-21

Status: `proposed`

Collaborators: @sumit_chachadi

Related: [013-chronicler-direct-kafka-python.md](013-chronicler-direct-kafka-python.md), [016-chronicler-kafka-consumer.md](016-chronicler-kafka-consumer.md)

## Context

GitHub Enterprise webhooks are HMAC-SHA256 signed (`X-Hub-Signature-256`). Incident.io webhooks are Svix-signed (`webhook-signature`). Both are verified inside `Chronicler.consumer.KafkaConsumerLoop._process_event` before the message is forwarded to Scribe + Enricher.

**Atlassian Jira Data Center (the Airbnb-hosted instance at `jirarest-stage.airbnb.biz` and `jirarest.airbnb.biz`) does not sign webhooks.** The webhook configuration UI exposes only:

- the destination URL
- a list of event-type filters (issue_created, comment_created, …)
- a JQL filter

There is no shared-secret field, no custom-header field, no HMAC option. The `JiraTransformer` in chronicler reflects this with `webhook_secret_key = ""`, which short-circuits the consumer's HMAC check entirely.

Additionally, **Jira DC is hosted on BizTech infrastructure** (`*.airbnb.biz`), not the prod AirMesh trust zone. Network-identity-based filtering (`airmeshOnly` on the Yoyo callback) is therefore not available — Jira's outbound POSTs to Yoyo are not AirMesh-identified principals. A BizTech-egress IP allowlist on Yoyo was considered but rejected: it depends on a Yoyo feature we have not confirmed exists, requires ongoing coordination with BizTech IT to track the egress range, and the resulting trust ("anything egressing from BizTech NAT") is coarse enough that it adds little above the two layers below.

The threat we care about: **a forged or spoofed Jira webhook** — someone outside Airbnb crafts a TCMR-keyed payload, POSTs it to the Yoyo callback URL, and gets a row written to Scribe plus an LLM enrichment call kicked off. We do not currently care about replay of legitimate events, prompt-injection through the Enricher, or full tenant takeover; those are documented for completeness in **Out of scope** below.

The Jira side is Airbnb-owned (IT can adjust the webhook config). We want a lightweight mitigation — no new infra dependencies, no per-event API round trips.

## Decision

Defense in depth via two stackable lightweight layers. Neither is as strong as an HMAC, but together they raise the bar from "anyone with the URL" to "an attacker with knowledge of the secret URL **and** full knowledge of TCMR-shaped payloads."

### Layer 1 — Secret-bearing callback URL

Replace the predictable Yoyo callback path

```
/v1/external/callback/matik/sandbox/jira/webhook/events
```

with a path that includes an unguessable secret segment:

```
/v1/external/callback/matik/sandbox/jira/webhook/events/<32-byte-hex>
```

The secret is provisioned through `secret-lair` (one per environment), wired into the Yoyo callback registration by syseng, and rotatable. Yoyo terminates the path; the chronicler never sees the secret. A scanner looking for `/v1/external/callback/matik/` won't get past this gate.

This is the only layer that gates *origin* — Layer 2 below gates *content shape*. The secret URL is the load-bearing piece of the design and must be rotated on a defined cadence.

### Layer 2 — Transformer-level sanity filters

The current `JiraTransformer.should_process` filters by `webhookEvent` ∈ accepted set and issue key starting with `TCMR-`. Extend it with cheap structural and provenance checks; any failure logs and drops the event:

| Check | Source of truth | Notes |
|---|---|---|
| `payload.user.emailAddress` ends in `@airbnb.com` / `@airbnb.biz` | Jira always populates `user` on outbound webhooks | Spoofed payloads from outside Airbnb won't pass without also knowing a real Airbnb email |
| `payload.issue.self` URL host matches the configured `JIRA_BASE_URL` host | Jira embeds the issue's REST URL in every webhook payload | Forged payloads from a different Jira tenant won't match |
| `payload.issue.fields.project.key == "TCMR"` (strict equality, not prefix) | Already roughly checked but only on issue key prefix | Cheap, removes ambiguity if other project keys ever start with `TCMR-` |
| `payload.timestamp` within the last 24 h | Atlassian sets `timestamp` (unix ms) on every webhook | Drops opportunistic replay of older captured events without needing a dedicated dedupe store |

These don't *prove* origin — an attacker who knows the schema could craft a payload that passes all four checks — but combined with Layer 1 they make a successful forgery require: (1) the secret URL, and (2) full knowledge of Atlassian's payload schema for our specific Jira tenant.

`webhook_secret_key = ""` stays — there's still no signature to verify. The consumer's `record_signature(provider, "skipped_no_scheme")` metric continues to fire on every Jira event for observability.

## Consequences

- **Stronger posture without a new dependency.** No DB lookup per event, no extra moving parts, no per-event LLM-cost hit, no cross-team network-config negotiation.
- **Layer 1 requires syseng coordination** to populate the secret in Yoyo's Jira callback config and to rotate it on schedule. Owners: `#api-infra` for Yoyo, IT for the Jira webhook URL.
- **Layer 2 is purely chronicler-side**, lands in `JiraTransformer.should_process`, ships in a follow-up PR.
- **No network-layer ingress restriction** is part of this design. Anyone on the public internet who learns the secret URL can submit payloads; the only thing standing between them and Scribe is then Layer 2. Rotation discipline on the URL secret matters accordingly.
- **Metric coverage:** the existing `matik_chronicler_signature_validations_total{provider="jira",result="skipped_no_scheme"}` series will continue to be the only signal that signature validation was bypassed. Once Layer 2 lands, add `matik_chronicler_jira_sanity_filter_total{check="..."}` to dashboard the rejection rate by individual filter.

## Out of scope (explicitly rejected for this ADR)

- **HMAC signature**: Atlassian Jira Data Center doesn't expose a signing scheme. Bringing one in would require either an Atlassian Marketplace app (heavy) or upgrading to Jira Cloud (organisational decision, not ours).
- **Network ingress restriction on the Yoyo callback** (`airmeshOnly` or BizTech IP allowlist): Jira DC sits on BizTech, outside the AirMesh trust zone, so AirMesh identity does not apply. A BizTech-egress IP allowlist is technically possible but depends on an unconfirmed Yoyo feature and ongoing coordination with BizTech IT, and the resulting trust grain ("anything egressing BizTech NAT") is coarse enough that Layers 1 + 2 already dominate the marginal protection it would add. Revisit if Yoyo grows first-class per-callback IP filtering.
- **Per-event Jira REST verification** (look up the issue, confirm it matches the payload): adds a Jira API round trip per webhook and creates a dependency loop (chronicler now needs Jira creds, which is precisely the historian's job). Rejected as too heavy for the threat model.
- **Replay protection beyond a 24 h timestamp window**: would need a dedupe store keyed on issue + timestamp. The historian already produces a daily snapshot that overwrites whatever Scribe persists, so the blast radius of a replayed event is small and self-healing.
- **Prompt-injection through the Enricher**: out of scope here. PII scrubbing and prompt hardening are the Enricher's responsibility; the same protections that handle malicious PR bodies cover this.

## Implementation order

1. **Layer 2 (transformer filters)** — independent code change, no infra coordination. Ship first.
2. **Layer 1 (secret URL path)** — syseng / `#api-infra` ticket after Atlassian's outbound-webhook capabilities are confirmed in writing for our Jira Data Center version, and after the secret-lair entry + rotation runbook are in place.

## Open items to confirm during implementation

- Does Atlassian Jira Data Center (our specific version) support adding a static custom header to outbound webhooks? If yes, we can carry a shared secret as `Authorization: Bearer <token>` and validate it inside `JiraTransformer.validate_signature` — promoting this ADR to "ships HMAC-equivalent" status and replacing Layer 1's URL secret with a header secret.
- Defined rotation cadence and owner for the Layer 1 URL secret.
