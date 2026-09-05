# DLQ Inspector

Interactive tool to **inspect** (read-only) or **redrive** messages from Matik
SQS dead letter queues (DLQs) across `sandbox`, `staging`, and `production`.

## Quick start

```bash
cd scripts
uv run python -m dlq_inspector
```

Or via the top-level dispatcher:

```bash
cd scripts
uv run python -m scripts dlq-inspector
```

The tool walks you through prompts: pick an environment, pick the DLQ(s), then
pick an action — **inspect** (default) or **redrive**.

## Actions

### Inspect (read-only)

A non-destructive peek. Messages are read with `VisibilityTimeout=0` so they
are **never hidden from other consumers and never deleted**.

1. Reads up to a configurable max number of messages per selected DLQ
   (default: 50). Enter `all` at the prompt to retrieve every message
   currently in the queue instead of capping at a fixed number. If a queue's
   approximate depth exceeds the chosen cap, the tool warns you and suggests
   re-running with `all` so the report reflects the full backlog. A progress
   bar tracks how many messages have been pulled so far.
2. Saves each raw message as JSON under:
   ```
   dlq-output/<env>/<queue-name>/<timestamp>_<message-id>.json
   ```
3. Writes a Markdown report at:
   ```
   dlq-output/<env>/report_<timestamp>.md
   ```
   summarising per-queue depth, `source_type` / `message_type` breakdowns, and
   sample payloads.

Output paths are relative to the directory you run the script from.

### Redrive (moves messages back to the source queue)

**Destructive** — moves messages out of the DLQ and back to its source queue.
Source queues follow the Matik naming convention (`-dlq` → `-queue`), which
matches the redrive policy already attached to each DLQ. A confirmation summary
shows the derived destination for each queue before anything moves.

Two modes:

| Mode | Mechanism | Filtering | Notes |
|------|-----------|-----------|-------|
| **manual** (default) | receive → filter → send → delete, one at a time | `source_type` and/or `message_type` | Supports a per-queue cap (or `all`), **dry-run** (default), and throttling. Deletes from the DLQ only after a successful send, so a failure can't lose data. Tags each redriven message so its outcome can be traced (see below). A progress bar tracks messages redriven so far. |
| **native** | AWS `StartMessageMoveTask` | none | Fast; moves **all** messages back using the redrive policy. Supports a messages/second throttle only. Cannot tag individual messages — AWS moves them without giving us a hook, so redriven messages can't be traced to an outcome this way. |

**`sqs:StartMessageMoveTask` IAM permission required for native mode.** If your
role only has the standard SQS queue/DLQ permissions (send/receive/delete/etc.,
no `StartMessageMoveTask`), use manual mode instead — it only needs those
standard permissions.

In **manual** mode you're prompted for:

- **Filters** — exact match on `source_type` and/or `message_type` (Enter = all)
- **Max messages** to redrive per queue (default: 50; enter `all` to redrive
  every message in the queue, no cap)
- **Dry run** — defaults to *yes*; shows what *would* move without changing anything
- **Throttle** — optional messages/second cap

### Tracing whether a redrive actually succeeded

Moving a message back to the source queue isn't the end of the story — it
still has to be reprocessed (e.g. by Scribe) before it's really "fixed."
There's no dedicated audit table for this; instead:

- Manual mode generates a **Redrive ID** (e.g. `20260813_120000`) once per run
  and stamps it as a `RedriveRunId` SQS message attribute on every message it
  sends back. It's printed in the confirm summary and the final "Done" line —
  copy it down.
- The consuming service picks up that attribute and tags its own metrics and
  logs with it. For Scribe specifically:
  - **Metrics** — `matik_scribe_messages_processed_total`,
    `matik_scribe_backoff_total`, and `matik_scribe_dlq_messages_total` all
    carry a `redriven="true"/"false"` label, so you can see aggregate
    success/failure counts for redriven messages in Grafana.
  - **Logs** — success and failure log lines include `redrive_run_ids`, so you
    can grep OpenSearch/Kibana for your Redrive ID to see exactly what
    happened to those messages, including the failure reason on error.
- Since Scribe flushes messages in groups and a group's outcome applies to
  every message in it uniformly, "did the group containing my redriven
  messages succeed or fail" is the same question as "did my redriven messages
  succeed or fail" — no per-message tracking needed.
- Native mode can't do any of this — there's no way to tag messages it moves,
  so use manual mode whenever you need to verify the outcome.

## Safety

- **Production** requires typing `yes` at an extra confirmation gate.
- Every action shows an "About to run" summary and a `[y/N]` confirm before
  touching AWS.
- Redrive defaults to a **dry run** (manual mode) — review the counts, then
  re-run with dry run disabled to actually move messages.

## Prerequisites

- **AWS credentials** in your environment. The script uses whatever profile
  boto3 resolves by default (env vars → `~/.aws/credentials` → instance
  metadata). To use a specific profile:
  ```bash
  AWS_PROFILE=airbnb uv run python -m dlq_inspector
  ```
- **boto3** — not in `scripts/pyproject.toml` by default (it's a heavy dep not
  needed by other scripts). If you hit an `ImportError`, add it:
  ```bash
  cd scripts && uv add boto3
  ```

## Queue catalogue

The DLQ URLs are hardcoded in `inspector.py` (`DLQ_CATALOGUE`), sourced from
`_infra/kube/kube-gen.yml`. If new queues are added, update that dict. Current
queues per environment:

- Scribe high-priority
- Scribe medium-priority
- Scribe low-priority
- Enricher
- Enigmatologist
