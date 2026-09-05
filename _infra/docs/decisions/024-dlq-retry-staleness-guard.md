# DLQ Retry Staleness Guard

Date: 2026-07-22

Status: `proposed`

Related: [018-scribe-dedicated-deployments-and-batching.md](018-scribe-dedicated-deployments-and-batching.md), [019-scribe-enrichment-no-batching.md](019-scribe-enrichment-no-batching.md), [023-generic-matik-webhook.md](023-generic-matik-webhook.md)

Collaborators: @alfredo-moreira

> Full exploration and rejected alternatives live in the Slate design discussion doc, [DLQ Retry Staleness Guard — Design Discussion](https://slate.airbnb.tools/Fd4696rS1k).

## Context

Matik's SQS pipeline (Chronicler/Historian/Enricher → Scribe → DB) can silently lose data. A message that fails and lands in a DLQ can be redriven hours later carrying **stale** content and clobber a row that fresher data already updated in the meantime. `scripts/dlq_inspector`'s manual redrive resends the raw message body verbatim and never calls Matik service code — a redriven message is indistinguishable from a fresh one once it's back on the queue. Redrive is also meant to be used sparingly (a deliberate, human-reviewed action, not an automated/continuous retry service), which lowers how often a redriven message races a fresher write, but doesn't remove the need for a fix — an infrequent race is still a real race.

Today: no Scribe message carries an "entered Matik" timestamp (`common/models/scribe_messages.py`, `EnrichmentRequest` in `common/models/enricher_messages.py`); no table has an ingest-time column distinct from `row_updated_at` (which is DB-managed via `ON UPDATE CURRENT_TIMESTAMP` and reflects when MySQL last touched a row, not which message won); and all writes in `common/daos/base_dao.py` — both the base upsert (`INSERT ... ON DUPLICATE KEY UPDATE`) and the enrichment update (`UPDATE ... WHERE pk = :id`) — are unconditional. Because a redriven message can't be distinguished from a fresh one, the fix cannot live in DLQ tooling and cannot be DLQ-specific; it has to be a general last-write-wins-by-entry-time guard applied to every write.

Scribe isn't the only SQS+DLQ in the system, and only Scribe ever writes to the DB — Enricher is a pure re-producer that consumes one message and emits a different one onward to Scribe. If Enricher re-stamps its outbound message's timestamp to "now" instead of forwarding the inbound one, a message stuck in *Enricher's* DLQ comes back out looking fresher than it deserves, reintroducing the same clobbering bug one hop upstream of Scribe.

## Decision

**Stamp an `entered_at` timestamp on every message, generated once at true origin, propagated unchanged thereafter.** Chronicler and Historian stamp it when they first observe a fact. Enricher forwards it into its outbound enrichment message unchanged — it never re-stamps.

**One `entered_at` timestamp column per write-group, not per row.** Several tables are written by multiple independent write-groups touching disjoint columns on the same row — e.g. `incidentio_incidents` is written by the `incidentio` base upsert, `incidentio`'s own enrichment (`root_cause_summary`/`description_summary`), and `incident_channel_summary`'s enrichment (`incident_channel_summary`). A single shared timestamp column would let an unrelated write on one column-group incorrectly block a write to a different, unrelated column-group. `DataSourceSpec` (`common/datasources/registry.py`) already partitions each table's columns by write-group (`update_columns` for base, `llm_columns` for enrichment, `exclude_columns` for columns another source owns); this extends that existing partition with a matching timestamp column per group.

**Every guarded DB write is conditional and atomic** — the freshness check and the mutation happen in one SQL statement, not a read-compare-write round trip, so two concurrent Scribe consumers (e.g. a normal message and a DLQ redrive processed in parallel) can't race past each other:
- Enrichment path (`_update_partial`): add the freshness predicate directly to the existing `WHERE` clause (`AND entered_at_col < :new_entered_at`, NULL-safe for first write) and write the timestamp column in the same `UPDATE`.
- Base upsert path (`_upsert_chunk`, `INSERT ... ON DUPLICATE KEY UPDATE`): since `ON DUPLICATE KEY UPDATE` has no `WHERE` clause, each guarded column's value expression becomes a SQLAlchemy `case()` (portable equivalent of MySQL's `IF()`) that only takes the incoming value when the incoming `entered_at` is newer than the stored one (or the stored one is NULL) — applied to the timestamp column too, so it only advances alongside a winning write.

**A stale-skip is a success, not a retry.** Today, `update_llm_fields_from_message` returning 0 rows affected is interpreted as `BaseRecordNotFoundError` (base row not written yet → long-delay retry). Once the write is conditional on staleness, a rejected-for-staleness update *also* produces 0 rows affected, which would be misread as "not found" and retried forever with the same losing payload. The DAO's tri-state return (`True`/`False`/`None`) gains a 4th distinct outcome (`SKIPPED_STALE`) so "skipped, already fresh" is acked by the handler, not misread as a retry trigger.

**Backward-compatible by construction.** A NULL stored timestamp, or a message with no `entered_at` (in-flight/DLQ messages that predate this change), falls back to an unconditional write — the guard never skips on missing data.

### Scope (v1)

Only the demonstrated race-risk tables get guard columns now:

| Table | Write-groups | Timestamp columns |
| --- | --- | --- |
| `incidentio_incidents` | `incidentio` base, `incidentio` enrichment, `incident_channel_summary` enrichment | `incidentio_base_entered_at`, `incidentio_enrichment_entered_at`, `incident_channel_summary_entered_at` |
| `ghe_pull_requests` | base, enrichment | `ghe_pr_base_entered_at`, `ghe_pr_enrichment_entered_at` |
| `jira_issues` | base, enrichment | `jira_base_entered_at`, `jira_enrichment_entered_at` |

**Out of scope for v1:** correlation tables. Enigmatologist's correlation outputs (`CorrelationGroupBaseMessage`/`CorrelationBaseMessage`) write to correlation tables that get no guard column yet, so no Enigmatologist propagation work happens now — deferred until those tables are onboarded. Note the ad-hoc `CorrelationRequest` API-triggered origin no longer exists in the codebase; that trigger now originates in `scribe/hooks/enigmatologist.py`, post-Scribe-write. Recorded here for whoever picks up correlation-table onboarding later.

### Why this scales to future services

The guard logic belongs in the generic layer (`BaseUpsertDAO` / `GenericHandler`), driven by two new `DataSourceSpec` fields (`base_entered_at_column`, `enrichment_entered_at_column`) — not hand-written per DAO. Onboarding a future service/write-group costs exactly what onboarding `incident_channel_summary` already cost: a migration for its own columns (now including its timestamp column) + a `DataSourceSpec` entry declaring them. Zero new handler/DAO code per new source.

## Consequences

- **New migration** adds 7 nullable `DATETIME` columns across the 3 tables above (chained from the current Alembic head).
- **`DataSourceSpec` gains two new optional fields**; the derived `update_columns`/`llm_columns` properties must auto-exclude `*_entered_at` columns so ordinary writes never clobber them — the guard is the only thing that sets them.
- **`common/daos/base_dao.py`'s partial-update return type changes** from a bare tri-state (`True`/`False`/`None`) to a 4-state outcome. Every caller of `update_llm_fields_from_message` (currently just `scribe/handlers/generic.py`) needs updating to handle the new `SKIPPED_STALE` branch.
- **`EnrichmentRequest` and all base/enrichment Scribe messages gain an optional `entered_at` field.** Optional (not required) specifically so in-flight and already-DLQ'd messages from before this deploy don't fail validation — they fall through to the backward-compat unconditional-write path.
- **Chronicler and Historian gain a stamping responsibility** at every message-construction call site for the in-scope sources; Enricher gains a forwarding responsibility at its one outbound-message construction site. Neither gains new configuration surface.
- **No behavior change for out-of-scope tables/sources** — this ADR only touches Incident.io, GHE PR, and Jira write paths.

## Out of scope (explicitly deferred)

- Correlation tables and Enigmatologist propagation (see Scope above).
- Rewriting the rejected alternatives (single JSON column per table; central write-provenance table) — see the Slate design discussion for why each was rejected for v1.
- Onboarding every Scribe-writable table at once — v1 intentionally covers only tables with demonstrated base+enrichment race risk; broader rollout is a follow-up once this lands.
