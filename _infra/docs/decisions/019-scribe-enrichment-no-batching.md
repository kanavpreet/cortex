# Scribe Enrichment Messages Are Not Batched

Date: 2026-05-12

Status: `accepted`

Collaborators: @alfredo-moreira

## Context

[018-scribe-dedicated-deployments-and-batching.md](018-scribe-dedicated-deployments-and-batching.md) introduced an in-process batching layer in Scribe. During a flush, messages are grouped by `(source_type, message_type)` and dispatched as a batch — one DB connection per group — to amortise connection checkouts across the base DAO methods (`upsert_incidents_batch`, `upsert_jira_issues_batch`, etc.) that accept a list of records and issue a single multi-row `INSERT … ON DUPLICATE KEY UPDATE`.

Scribe processes two `message_type` values per source:

| `message_type` | Producer | What it carries |
|---|---|---|
| `base` | Historian / Chronicler | Full domain record (incident, JIRA issue, PR, …) |
| `enrichment` | Enricher | LLM-generated fields for one specific record (summaries, hashes) |

When the batcher flushes, it partitions the snapshot into two paths (`batcher.py:206–235`):

```python
if bm.parsed["message_type"] == "enrichment":
    enrichment_singles.append((key, bm))   # dispatched one task per message
else:
    base_groups[key].append(bm)             # dispatched one task per group
```

Enrichment messages are dispatched as **single-element tasks** rather than grouped with their siblings. This document records why.

### Why batching is possible for base messages

Base messages share a DAO path that is genuinely batchable. `IncidentIOIncidentDAO.upsert_incidents_batch` (and equivalent batch DAOs) accept a `list[T]` and issue one `INSERT … ON DUPLICATE KEY UPDATE` statement covering all rows in the list (chunked at `BATCH_SIZE=500`). A single DB connection handles the entire group; acknowledgement of all messages in the group happens together after the batch upsert succeeds.

### Why the same logic does not apply to enrichment messages

**1. No equivalent batch DAO operation.**
The enrichment DAO path calls `update_llm_fields(incident_id, root_cause_summary_hash, description_hash, …)` — a single-row `UPDATE` keyed on `incident_id`. Each enrichment message targets a *different* incident with a *different* set of LLM-generated values. There is no multi-row UPDATE form that would reduce round-trips the way a multi-row INSERT does, so grouping enrichments in a batch provides no connection amortisation benefit.

**2. `BaseRecordNotFoundError` must be isolated per message.**
Enrichments are produced by the Enricher immediately after LLM jobs complete. The corresponding base record is written to the database by Scribe only when its SQS message reaches the front of the queue and is flushed. Under normal load the base write lands first, but there is no hard ordering guarantee — a low-priority base message may still be buffered or inflight when the Enricher's enrichment message arrives.

When `update_llm_fields` is called for an `incident_id` that does not yet exist in the database, the DAO returns `False` and the handler raises `BaseRecordNotFoundError`. The batcher's response is to extend the SQS visibility timeout by `enrichment_base_not_found_delay` so the message is retried after the base record has had time to land (`batcher.py:277–291`).

If enrichments were batched into a group, *one* not-yet-ready record would trigger `BaseRecordNotFoundError` for the entire group, extending the visibility timeout on every sibling — including enrichments whose base records are already present and ready to be written. This would artificially stall unrelated enrichments and grow the medium-queue backlog during any sustained base-write lag.

By dispatching each enrichment as an independent single-element task, a single blocked enrichment cannot delay or poison its siblings in the same flush.

**3. Enrichments are inherently independent.**
Base records in a flush batch are related only by `source_type`; batching them is a pure throughput optimisation. Enrichment records have no such structural relationship — they are produced independently per incident by the Enricher, arrive in arbitrary order, and have no shared success or failure condition. Treating them individually matches their semantics.

### Current behaviour under load

At steady state, the `enrichment_singles` path produces one `_flush_group` task per enrichment message received in a flush window. Under the medium lane's default configuration (50-message buffer, 2s flush interval) this means up to 50 simultaneous enrichment tasks per pod, each holding a semaphore slot and one DB connection. The `max_concurrent_writes` semaphore (default 10 on the medium lane) bounds actual concurrency.

## Decision

Retain the existing split in `batcher._flush_now`: base messages are grouped and flushed as a batch; enrichment messages are dispatched one task per message. Do not add a batch DAO operation for enrichments.

## Consequences

### Positive

- **Failure isolation.** A `BaseRecordNotFoundError` on one enrichment extends only that message's visibility timeout; all other enrichments in the same flush proceed normally.
- **Correct semantics.** Each enrichment's success or failure is independent; the single-task model reflects that accurately.
- **No speculative complexity.** A batch `UPDATE` form for enrichments would require additional DAO code and schema constraints with no throughput benefit, since the bottleneck in the enrichment path is the upstream LLM job rate, not DB write throughput.

### Negative / Risks

- **No connection amortisation for enrichments.** Each enrichment task checks out its own DB connection. Under enrichment-heavy load the medium lane can approach `max_concurrent_writes` connections per pod on enrichments alone, leaving fewer slots for base messages sharing the same lane. Mitigated by the semaphore cap and the observation that enrichment volume is bounded by LLM job throughput.
- **Higher SQS delete call rate.** Acking enrichments one-by-one produces one `delete_message` call per message rather than one per batch. At the expected enrichment rate this is within SQS API limits.

## References

- [`018-scribe-dedicated-deployments-and-batching.md`](018-scribe-dedicated-deployments-and-batching.md) — batching strategy and flush mechanics
- `matik/scribe/batcher.py:183–235` — `_flush_now` partition logic
- `matik/scribe/handlers/incidentio.py:72–113` — `handle_enrichment_batch` and `BaseRecordNotFoundError` raise site
