# Root Cause Coverage Audit — Review Guide

## What this is

Every day at 06:00 UTC, the audit checks newly closed/canceled Biztech incidents. It tries to find and verify a root-cause change directly from the incident text:

- PR (GitHub Enterprise pull request)
- TCMR (Jira change record)

An LLM extracts a citation and resolves it against GHE/Jira. If it can’t find one or can’t verify what it found, the incident is marked for a human to review. This guide covers that human step.

For the full design, see:
- [automated-incident-audit.md](../architecture/automated-incident-audit.md)
- [DD 025](../decisions/025-automated-root-cause-coverage-audit.md)

Where you work:
- Sheet: Matik Automated Audit
  https://docs.google.com/spreadsheets/d/1ubO5lEca1gJh-_VJnWAzY4ycVhssVKtk-8ZRlUBW9LM
  - incidents tab — one row per incident
  - correlations tab — correlations for concluded incidents

Editable columns (everything else is locked to the pipeline's service account):
- **incidents** tab: `audit_status`, `change_related`, `ownership`, `source_traceable`, `human_gold_entity`, `human_review_notes`
- **correlations** tab: `classification`, `human_verified`

---

## Your workflow (step-by-step)

1) Find rows that need you
- Go to the incidents tab.
- Filter `audit_status` = `needs_human_review`.
- Read `description`, `channel_summary`, and `reasoning` (why the LLM stopped).
- Also check `cited_identifier` and quote_span if present — the LLM may have spotted a real citation but failed due to formatting.

2) Decide the outcome (pick exactly one)
- A. There is a real PR or TCMR that is the root cause
- B. There is no PR/TCMR root cause (vendor issue, hardware failure, unknown, or internal gap with no tracked change)

3) Do the update

    Both outcomes end the same way: once you're done, set `audit_status = human_reviewed` yourself. That's the one signal the pipeline waits for — nothing is picked up on the next run until it sees it, whether or not `human_gold_entity` is filled in.

    Outcome A — **real root cause found**:
    - Enter `human_gold_entity` with exactly one of:
      - Full GHE PR URL: `https://github.airbnb.biz/<org>/<repo>/pull/<number>`
      - TCMR key: `TCMR-<number>`
    - Do not enter:
      - Bare PR numbers: #1234
      - org/repo#1234 (no surrounding context exists for the pipeline to resolve)
    - Set `audit_status` = `human_reviewed`.
    - What happens next:
      - On the next run, the pipeline verifies it like any LLM citation.
      - **If verified:** `overall_status` becomes `final` and the row is scored.
      - **If not verified:** nothing changes visibly, and the row stays ongoing. Re-check the exact format above and try again.

    Outcome B — **no PR/TCMR root cause**:
    - Leave `human_gold_entity` blank.
    - Set `audit_status` = `human_reviewed`.
    - What happens next:
      - On the next run, any correlations are classified (never root_cause; there is nothing to have caught).

4) Optional but helpful
- `human_review_notes`: add brief context (why Matik did/didn’t catch it; anything notable).
- You may also correct:
  - `change_related`, `ownership`, `source_traceable`
  - These are LLM-written and are not regenerated on rescore; your edits stick — with one exception: `source_traceable` is forced back to `TRUE` if a verified root cause is later established (whether by the LLM or by you filling in `human_gold_entity`), since a verified root cause is always traceable by definition.

`source_traceable` answers: even without a specific PR/TCMR, is there some external source we could point to for this root cause (a vendor status page, a change log, etc.)? The LLM sets a starting guess:
  - `TRUE` — a root cause was verified, or (unverified) it's a vendor outage reflected on a public status page.
  - `FALSE` — a vendor change with no visibility into it, an untracked manual change, hardware failure, or performance degradation.
  - blank — genuinely ambiguous (e.g. an unplanned power outage); fill it in yourself.

You’re done once the appropriate update is saved.

5) Clear and verify correlations

Each time you review incidents, also review the **Correlations** tab and clear any rows where `human_verified` is blank.

Correlations are written only after an incident reaches a conclusion. For hand-resolved incidents, this happens on the **run after** you set `audit_status = human_reviewed`. Therefore, the incident you mark `human_reviewed` during the current visit will not have correlations yet; those correlations should be reviewed by the next reviewer.

1. Go to the **Correlations** tab.
2. Filter to rows where `human_verified` is not `TRUE`.
3. For each row, compare the correlation against the corresponding incident in the **Incidents** tab and classify it:

   * **`root_cause`** — The correlation's `entity_type` and `entity_id` match the incident's `gold_entity_type` and `gold_entity_id`.
   * **`relevant`** — It is not the gold entity, but the correlation's `services` matches or is meaningfully related to the incident's `incident_root_cause_service` or `incident_affected_services`.
   * **`noise`** — It is neither the gold entity nor relevant to the incident's root-cause or affected services.
4. If the pipeline's classification is incorrect, correct it.
5. Set `human_verified = TRUE` for every reviewed row.

This means the Correlations tab should be left with **no unverified rows** after each review session, while newly generated correlations will be picked up during the next visit.


---

## Quick reference — accepted values

| Tab | Column | Possible values |
|---|---|---|
| incidents | audit_status | needs_human_review, llm_reviewed, human_reviewed, error |
| incidents | overall_status | ongoing, final |
| incidents | incident_status | closed, canceled |
| incidents | change_related | TRUE, FALSE |
| incidents | ownership | airbnb, vendor, unknown |
| incidents | source_traceable | TRUE, FALSE, or blank (ambiguous — human decides) |
| incidents | gold_entity_type | github_pr, jira_tcmr, or blank (no verified gold entity) |
| incidents | root_cause_covered_postmortem / root_cause_covered_investigation | TRUE, FALSE, or blank (not applicable — no verified gold entity) |
| incidents | root_cause_service / affected_services | incident.io's own custom fields, as free text (comma-separated for affected_services) |
| correlations | time_bucket | investigation, postmortem |
| correlations | source | braintrust (investigation-time), database (postmortem-time) |
| correlations | classification | root_cause, relevant, noise |
| correlations | services | the correlation's own service(s), comma-separated |
| correlations | url | direct link to the source (PR, TCMR, etc.), blank if the source has none registered |
| correlations | base_llm_score | the correlation engine's own confidence score (0-1) for this candidate, blank for a service-matched candidate (no LLM score exists) |
| correlations | human_verified | TRUE, or blank (not yet reviewed) |

---

## Reading the correlations tab

- Rows appear here only after the incident reaches a conclusion (llm_reviewed or human_reviewed).
- A `needs_human_review` incident will have no rows — that’s expected.
- `time_bucket`: `investigation` (first N minutes) or `postmortem` (full history).
- `source`: `braintrust` (investigation-time) or `database` (postmortem-time).
- `classification`: `root_cause`, `relevant`, or `noise` — root_cause is an exact match against the verified gold entity; relevant vs. noise is decided deterministically by service overlap with the incident (never an LLM judgment call). See [automated-incident-audit.md](../architecture/automated-incident-audit.md) for the full rule.
- `services`: the correlation's own service(s). `url`: a direct link when the source has one registered (currently GHE PRs and Jira TCMRs).
- `human_verified`: blank until a reviewer checks the row against `classification`/`reasoning` and marks it `TRUE` (see step 5 above). Purely manual — the pipeline never writes or re-checks this column.

---

## Error rows

- `audit_status` = `error` means correlation fetch/classification failed after the root cause (or confirmed absence) was already resolved.
- These typically indicate Braintrust RBAC or infra issues, not extraction problems.
- They retry automatically on each run.
- If `error_detail` repeats across multiple runs, open `error_log_url`, check the failing pod’s logs, and troubleshoot further.

---

## Cadence and expectations

- The audit runs daily (06:00 UTC).
- `needs_human_review` rows will accumulate if not processed.
- Please check in regularly; the LLM only misses once, but delays on human follow-up reduce coverage.
