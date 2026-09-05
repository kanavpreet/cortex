# Historian Failure Handling

## Incident.io

**Tracker table:** `incidentio_tracker` (single row, `id=1`)

**On failure:**
- Sets `status = "ERROR"` and saves `last_updated_at_cursor` with the exact fetch position
- Next run detects ERROR state and **refuses to run** — requires manual intervention

**Recovery:**
1. Investigate the error via `error_message` in the tracker row
2. Fix the root cause
3. Reset the tracker: `UPDATE incidentio_tracker SET status = 'OK' WHERE id = 1`
4. Next run resumes from `last_updated_at_cursor`, then clears it on success

## GitHub (GHE PRs)

**Tracker table:** `ghe_pr_tracker` (one row per org/repo)

**On failure:**
- No error state saved — the tracker is write-only and never read back on subsequent runs
- The cutoff is recalculated as `now() - lookback_days` every run regardless of tracker state
- Failed repos are skipped; other repos continue processing

**Recovery:**
- No manual intervention needed — next run retries the full lookback window automatically
- Hash cache prevents duplicate LLM calls for already-processed PRs
- Known issue: tracker doesn't actually resume from where it left off

## Jira

**Tracker table:** `jira_batch_tracker` (one row per ticket type: TCMR, operational)

**On failure:**
- Sets `status = "ERROR"` with error message
- Next run detects ERROR state and **refuses to run** — requires manual intervention
- If the process crashes mid-batch (`status = "PROCESSING"`), next run resumes the same batch window

**Recovery:**
1. Investigate the error via `error_message` in the tracker row
2. Fix the root cause
3. Reset the tracker: `UPDATE jira_batch_tracker SET status = 'OK' WHERE ticket_type = '<type>'`
4. Next run resumes from the saved batch window
