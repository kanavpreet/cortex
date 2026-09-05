---
description: Scaffold a new Matik eval (summary or correlation) wired to the production prompt
argument-hint: <what to eval, e.g. "incidentio root cause summary" or "a new correlation anchor">
---

You scaffold a new **eval** under `matik/evals/<category>/<type>/`: a thin `eval.py`
wrapper, a `golden_dataset.yml` of fictional rows, and a thin `seed_dataset.py`
loader. The eval must test **real production behavior**, so every wiring value is
read from existing config — never invented.

Read `_infra/docs/development/offline-evals.md` first for the framework overview. Then follow
the steps in order. `$ARGUMENTS` is what the user wants to eval.

## Step 1 — Pick the category

- **summary** — mirrors one entry in the enricher's `source_mappings` (a per-type
  prompt that turns source text into a summary). Most new evals are summaries.
  `GoldenRow` = `{input: dict[str, str], expected: str, metadata}`; scorers:
  `summary_quality`, `faithfulness`, `pii_safe`.
- **correlation** — a new *anchor* under `evals/correlations/<anchor>/` (e.g. beyond
  `incident`). All anchors share the single `enigmatologist.correlation_system_prompt`
  and run the real generator; you supply a candidate pool and the causal answer.
  `GoldenRow` = `{input: incident + candidate events, expected: {correlated_ids,
  score_bands}, metadata}`; scorers: precision/recall/f1 + structure/calibration/reasoning.

Everything below is shared; **(summary)** / **(correlation)** callouts mark the
differences.

## Step 2 — Derive the wiring from config

**(summary)** Open `matik/local-configs/matik-enricher-config.yml` and find the
`enricher.source_mappings.<source>` entry (`incidentio`, `ghe_pr`, `jira`, …) whose
`output_field` matches the type. Record verbatim: **`input_keys`**, **`output_field`**
(the eval's `operation`), and the **`prompt`** text. Then find the flat **`prompt_key`**
in `_infra/kube/kube-gen.yml` under `common.all.params.enricher.source_mappings` whose
text matches (e.g. `incidentio_description_prompt`). If the type isn't in the config,
stop — there's no production prompt to evaluate.

The eval tests the **assembled** prompt — the enricher's `general_prompt` template
with the source prompt substituted into `{source_instructions}`. PII scrubbing, output
format, and anti-hallucination / "match length to content" live in `general_prompt`,
so the per-type prompt (and your `_CRITERIA`) only carry the type-specific intent.

**(correlation)** No per-type prompt to find — every anchor uses
`enigmatologist.correlation_system_prompt` (read live from kube-gen) and the real
generator/model (`enigmatologist.llm_provider`). What you must define is the **input
shape**: the incident fields plus the candidate `github_events` / `jira_events` pool.
Mirror `evals/correlations/incident/` and its `GoldenRow` in
`evals/correlations/_common.py`.

## Step 3 — Confirm names with the user

Match the sibling directories:
- **Directory**: clear app-and-purpose name (`incidentio_description`, `github_pr_summary`;
  prefer the app name over the internal source key).
- **`eval_slug`**: the directory name in dashes.
- **`dataset_name`**: built as `f"{CATEGORY}-<eval-slug>-golden"` (`CATEGORY` is
  `"summary"` or `"correlation"`) — don't hardcode the prefix.

Propose directory + eval_slug and confirm before creating files.

## Step 4 — Create `eval.py` (thin wrapper)

Model it on a sibling. It's the only place the two categories differ in wiring.

**(summary)** — `matik/evals/summaries/incidentio_description/eval.py`:
```python
from evals.summaries._common import CATEGORY, run_eval

_CRITERIA = "Focus on whether the summary <type-specific intent>, grounded only in the source."


def main() -> None:
    run_eval(
        eval_slug="<eval-slug>",
        dataset_name=f"{CATEGORY}-<eval-slug>-golden",
        prompt_key="<prompt_key>",
        input_keys=[<input_keys>],
        operation="<output_field>",
        criteria=_CRITERIA,
    )
```
`_CRITERIA`: one sentence, type-specific intent only. Don't restate length, PII, or
"don't hallucinate" — those come from `general_prompt` and the scorers.

**(correlation)** — `matik/evals/correlations/incident/eval.py` (no prompt/criteria;
the prompt, model, and scorers are all handled inside `correlations.run_eval`):
```python
from evals.correlations._common import CATEGORY, run_eval

_EVAL_SLUG = "<eval-slug>"


def main() -> None:
    run_eval(eval_slug=_EVAL_SLUG, dataset_name=f"{CATEGORY}-{_EVAL_SLUG}-golden")
```

Both files end with the standard `if __name__ == "__main__": main()` and a usage
docstring (copy a sibling's).

## Step 5 — Create the golden data + `seed_dataset.py`

Rows live in `golden_dataset.yml` (data separate from code); `seed_dataset.py` is a
**thin loader**. Use the shared `GoldenRow` from the category's `_common.py` — do NOT
redefine per-file TypedDicts or hand-roll the seeding.

**`seed_dataset.py`** (model on a sibling):
```python
from typing import cast

from evals._common import load_golden_rows, seed_golden_dataset
from evals.<category>._common import CATEGORY, GoldenRow

DATASET_NAME = f"{CATEGORY}-<eval-slug>-golden"
DATASET_DESCRIPTION = "Fictional golden dataset for the <type> eval (...)."

GOLDEN_ROWS: list[GoldenRow] = cast("list[GoldenRow]", load_golden_rows(__file__))


def main() -> None:
    seed_golden_dataset(
        dataset_name=DATASET_NAME,
        description=DATASET_DESCRIPTION,
        category=CATEGORY,
        rows=GOLDEN_ROWS,
    )
```

**`golden_dataset.yml`** — a list of rows; start with the `⚠️  FICTIONAL DATA — NOT
REAL …` header comment (copy a sibling's). Row shape:
- **(summary)** each row: `input` (dict, keys == `input_keys` exactly), `expected`
  (the gold summary string), `metadata` (unique `reference_id` + any type fields).
- **(correlation)** each row: `input` (incident fields + `github_events` /
  `jira_events` candidate lists), `expected` (`correlated_ids` + `score_bands`),
  `metadata` (`reference_id`, `scenario`).

Data rules — **without exception**:
- **Fictional only.** Invent every name, id, host, date, detail.
- **`expected` may contain ONLY what the model can see in `input`.** Never reference
  anything living only in `metadata` (e.g. the repo name) or inferred from outside the
  input — the model never receives `metadata`, so such a gold rewards hallucination.
  (This bit us: a PR gold said "in the admin cookbook" when only `original_description`
  is sent.)
- **Realistic inputs.** Model real ticket/PR/incident/change text — no contrived
  filler (NOT "Jane Doe is required to collaborate on TCMR-123"). Where PII is needed,
  let it appear naturally in a real change (e.g. "Grant <name> prod DB access") and
  have the gold refer to the person by role.
- **~20 rows**, including **a few sparse rows** (thin input) for the hard cases.
- **2–3 rows with fake PII** in the `input` so `pii_safe` (summary) / the PII check
  (correlation) has something to catch; **every `expected` is PII-free**.
- **Unique `metadata.reference_id`** per row.
- **(correlation only)** every candidate must be timestamped **before** the incident;
  every `correlated_id` must exist in that row's candidate pool and have a `score_band`;
  include a **no-correlation** row and a **distractor** row (a candidate that must NOT
  be selected), and mix PR-only / TCMR-only / both-source rows.

## Step 6 — Register in the structural test

Add the new dataset to the category's `seed_dataset_test.py`:
- **(summary)** append `("<type>", [<input_keys>])` to `_DATASETS` in
  `matik/evals/summaries/seed_dataset_test.py`.
- **(correlation)** the tests in `matik/evals/correlations/seed_dataset_test.py` import
  the anchor's `seed_dataset` — point them at the new module (or generalize if adding a
  second anchor).

## Step 7 — Verify

```bash
cd matik/
uv run ruff check evals/<category> && uv run ruff format evals/<category>
uv run mypy evals/<category>
uv run pytest evals/<category> -o addopts=""
```
All must pass. Fix issues before reporting done.

## Step 8 — Report

Tell the user what you created and the wiring you derived. Remind them the golden rows
are **AI-drafted fiction** and should be **reviewed** before seeding, then point them at:
```bash
uv run python -m evals.<category>.<type>.seed_dataset   # upload to Braintrust
./scripts/run_eval.sh                                   # run it
```
Do not seed or run evals yourself unless asked (they need Braintrust/IAP access and
upload data). Do not commit unless asked.

If this eval covers a **summary** operation that passive monitoring doesn't score
yet, tell the user `SUMMARY_OPERATIONS` in `ml_models`'s `summaries.py` may need the
new operation added too, so it also gets continuously monitored in production — see
`_infra/docs/development/passive-monitoring.md` ("Adding a new summary operation").
That's a separate repo, so don't make that change yourself unless asked. Also remind
them: the `faithfulness`/`pii_safe`/reasoning rubrics in `evals/*/scorer.py` are
copied into `ml_models`'s passive-monitoring code, not shared — if this eval's work
involved tuning one of those rubrics, the same change needs to be manually ported
there too.
