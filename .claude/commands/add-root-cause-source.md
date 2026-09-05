---
description: Detect enigmatologist correlation categories not yet mirrored in the root-cause-coverage audit, and scaffold the missing source
---

You help extend the root-cause-coverage audit (`matik/audit/root_cause_coverage/`) to
recognize a new root-cause source type. The audit only knows about the sources
registered in `matik/audit/root_cause_coverage/sources/` (currently `github_pr`,
`jira_tcmr`) — see `_infra/docs/development/root-cause-audit-adding-a-source.md`
for the full design rationale before making any changes.

This is always manually triggered — nothing watches for changes automatically.
Run it whenever `enigmatologist` gains a new correlation category and the audit
needs to catch up.

## Step 1 — Find every category enigmatologist currently supports

Read `matik/enigmatologist/correlations/reliability/incident/state.py`'s
`ServiceCorrelationResult`/`LLMCorrelationResult`/`CorrelationGroupResult` models —
each field on them (e.g. `biztech_github`, `jira`) is a correlation category.
Cross-check against the `for category in (...)` loop in
`parse_llm_correlations` (`matik/enigmatologist/correlations/reliability/incident/nodes.py`)
and the category list in `_build_user_prompt` in the same file — all three
should agree on the same set of categories.

## Step 2 — Find every source the audit currently covers

Read `matik/audit/root_cause_coverage/sources/` — each module registers one
`RootCauseSourceSpec` with a `correlation_engine_field` (e.g. `"biztech_github"`).
Collect the set of `correlation_engine_field` values across all registered specs.

## Step 3 — Diff and report

Compare the two sets. Report to the user:
- Any enigmatologist category with no matching `correlation_engine_field` in the
  audit's registry (a real gap — the audit is silently blind to that source's
  investigation-time correlations).
- Any audit spec whose `correlation_engine_field` doesn't exist on enigmatologist's
  side either (stale registration, worth flagging separately).

If there's no gap, say so and stop — don't scaffold anything.

## Step 4 — Confirm before doing anything

List the gap(s) found and ask the user which one (if any) to add. Do not proceed
without an explicit confirmation of which source to scaffold.

## Step 5 — Gather what can't be inferred

Ask the user for:
1. **What the identifier looks like in incident text** (a URL pattern, a key
   format like `TCMR-\d+`, etc.) — needed to write the `parse` function's regex.
2. **How to verify it's real** — which existing client (or a new one) resolves
   the identifier against its real source, and what the verified entity's
   `entity_id` should be. If enigmatologist's own correlation-writing code uses
   an opaque id shape (like `github_pr`'s GHE PR id, not the human PR number),
   find and match that shape exactly — a mismatch here silently never matches a
   real correlation row. Check `matik/enigmatologist/correlations/reliability/incident/nodes.py`
   for how it writes `ReliabilityCorrelation.entity_id` for this category.
3. **The entity_type name** — the string this audit will use everywhere
   (`ReliabilityCorrelation.entity_type`, `VerifiedEntity.entity_type`).

## Step 6 — Scaffold the new source module

Create `matik/audit/root_cause_coverage/sources/<entity_type>.py`, following the
exact shape of `github_pr.py` or `jira_tcmr.py` (whichever is the closer
analog): a `parse(cited_identifier, quote_span) -> ... | None` function, a
`verify(context, parsed) -> VerifiedEntity | None` function that catches its
own exceptions and returns `None` on failure, an optional `build_links(entity_ids,
context) -> dict[str, str]` function (batched -- one lookup per run, not per
row -- for the Correlations sheet's link column; omit entirely if this source
has no linkable identifier), and a `register_root_cause_source(RootCauseSourceSpec(...))`
call at module import time. Add the new module to the import list in
`matik/audit/root_cause_coverage/sources/__init__.py`.

Also add a test file for the new module (e.g. `sources/<entity_type>_test.py`)
covering `parse` and `verify` directly, plus update
`ground_truth_test.py`'s `verify_ground_truth` tests to cover the new source
end to end.

While grepping for `jira_tcmr` as a reference pattern, note that
`RawPullRequest.jira_tcmr_key` (in `common/models/ghe_pr.py`) is an unrelated
field — a PR's linked TCMR key at the data-ingestion layer, not this audit's
`entity_type`. Don't mistake it for a second registration site.

If the verifier needs a client the pipeline doesn't already construct, that
client needs to be added as a field on `RootCauseAuditContext`
(`matik/audit/root_cause_coverage/pipeline.py`) and constructed in
`matik/audit/root_cause_coverage/main.py`, alongside the existing
`ghe_client`/`jira_client` construction.

## Step 7 — Draft the prompt update, for the user to review

The ground-truth extraction is purely extractive — the LLM can only cite an
identifier shape it's told to look for. Draft updated wording for
`ground_truth_extraction_prompt` (defined in `_infra/kube/kube-gen.yml` --
check every `root_cause_audit:` block that sets it, not just one) that
mentions the new identifier format, matching the existing prose style.
Present the diff and wait for the user to approve, edit, or reject it before
applying — don't write it directly into `kube-gen.yml` without that
confirmation.

Relevance (relevant vs. noise) is decided deterministically by service
overlap (`classify_by_service_match`), not by a prompt, so nothing to draft
there for a new source.

## Step 8 — Verify

From `matik/`, run `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy .`, and `uv run pytest` — the new source's test file and the
updated `ground_truth_test.py`/`classification_test.py` cases must pass, not
just exist.

Then run `pre-commit run --all-files` from the repo root and fix anything it
flags (formatting, lint, type-check, the config/env-var validators, and the
`unit-tests-matik` hook re-running the suite). Don't consider the change done
until both the test run and `pre-commit run` are clean.
