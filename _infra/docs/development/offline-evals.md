# Offline Evaluations Guide

How to run and create offline evaluations for Matik's LLM-backed features. See
[LLM Evaluations](../architecture/llm-evaluations.md) for more information on the LLM evaluation architecture.

## What an eval is

An eval runs an LLM step against a curated **golden dataset** and scores the output
with an **LLM judge**. Each run uploads to [Braintrust](https://gai.airbnb.tools)
(project `BizTech: Matik [Team: 791]`), where runs can be compared side-by-side to
see whether a prompt or model change improved or regressed the output.

Every Matik feature that uses an LLM should have an eval covering it.

## Do I need an eval?

**If an LLM produces it, and its output matters, it needs an eval.** Answer yes to
any of these and you need one:

- Does an LLM generate, classify, extract, or decide something?
- Could a prompt or model change silently make the output worse?
- Do you care whether the output is accurate, complete, or free of PII?

Deterministic, rule-based logic (no LLM) does not need an eval.

## What has evals

Evals are organized into two categories: **summaries** and **correlations**. Each
eval reads the live production prompt from `_infra/kube/kube-gen.yml` and feeds the
LLM the same input the service sends in production, so it tests the real behavior.
All scores are 0.0–1.0, higher is better.

### Summary scores (the enricher's summaries)

- **`summary_quality`** — accuracy/completeness/conciseness vs. the gold summary.
- **`faithfulness`** — every claim is grounded in the source (no hallucinations).
- **`pii_safe`** — binary: `1.0` = no PII leaked.

The judge defaults to **Bedrock Claude Opus 4.8**, a different model family
than the Facade/GPT generator, to avoid self-bias.

### Correlation scores (the enigmatologist's incident correlations)

Correlation is a retrieval problem: given an incident and a pool of candidate
changes (PRs and TCMRs), the LLM picks which ones are causal. Each golden
example has a known set of correct IDs, so most scoring is deterministic —
comparing the LLM's picks against that set. An LLM judge separately scores the
reasoning behind each pick:

- **`correlation_precision`** — of the changes it correlated, how many are truly
  causal (penalizes false positives).
- **`correlation_recall`** — of the truly-causal changes, how many it found.
- **`correlation_f1`** — harmonic mean of precision and recall.
- **`output_structure`** — binary: did the raw LLM output match the required JSON
  contract (catches format drift).
- **`score_calibration`** — did the correlations' confidence scores land in the
  expected band per gold ID.
- **`reasoning_quality`** — LLM judge: is each correlation's reasoning grounded and
  non-speculative.

The correlator generates on **Bedrock/Claude**, so its judge defaults to
**Facade/GPT** — the opposite pairing from summaries, again to avoid self-bias.

> Golden data committed to the repo is **fictional** — synthetic rows inspired by
> real events, with all PII faked.

> ⚠️ **Keep in sync with passive monitoring.** The `faithfulness`/`pii_safe`/
> reasoning rubrics above are copied into `ml_models`'s passive-monitoring
> code — they are not shared code. If you change a rubric in
> `evals/summaries/scorer.py` or `evals/correlations/scorer.py`, manually
> port the same change to the matching file in `ml_models`. Otherwise offline
> evals and passive monitoring will silently score against different
> definitions of "good."

## Prerequisites

- **IAP token** (to call the LLM locally) — `run_eval.sh` fetches this for you;
  needed manually only for direct `uv run`:
  ```bash
  airtool install iap-auth
  export IAP_TOKEN="$(iap-auth https://llm-fusion-hub.a.musta.ch)"
  ```
- **Braintrust / GenAI Studio access** to [gai.airbnb.tools](https://gai.airbnb.tools).
  Request access via [Gandalf](https://gandalf-lite.airbnb.tools/permissions/business_tools/braintrust-ai/braintrust-ai).
  The SDK authenticates via Airbnb SSO — **no API key**. (Seeding needs only this,
  not an IAP token.)
- **Tooling:** `uv` (`cd matik && uv sync`), `gum` (`brew install gum`), `iap-auth`.

See [facade.md](./facade.md) for LLM client / IAP details.

## Run an eval

```bash
cd matik/
./scripts/run_eval.sh          # interactive: pick category → eval(s) → judge
```

It walks you through selecting evals, fetches a fresh IAP token, and uploads each
run to Braintrust.

Or run one directly:

```bash
export IAP_TOKEN="$(iap-auth https://llm-fusion-hub.a.musta.ch)"
uv run python -m evals.summaries.incidentio_description.eval --experiment "prompt-v2"
```

The experiment name always starts with the category, eval name, and a timestamp
(`<category>-<eval>-<timestamp>`). `--experiment <suffix>` appends an optional
label, giving `<category>-<eval>-<timestamp>-<suffix>`.

Useful flags: `--experiment <suffix>` (label describing what changed),
`--description`, `--judge-provider {bedrock,facade}`, `--judge-model <id-or-alias>`.

## Seed (upload) a dataset

Existing evals already have their golden dataset seeded in Braintrust, so you
don't need to do this for normal use. Seed only when creating a new eval, or
when updating an existing golden dataset (e.g. adding or changing rows).
Seeding upserts by id, so re-running is safe.

```bash
cd matik/
./scripts/seed_dataset.sh                                      # interactive
# or:
uv run python -m evals.summaries.incidentio_description.seed_dataset
```

## When to run an eval

Run the relevant eval whenever a change could alter an LLM's output. **Run one if
you change any of these:**

- The **prompt** for an LLM step.
- The **model** or its parameters.
- The **input** sent to the LLM (e.g. which fields are included, or how they're
  formatted).

You don't need to run one for changes that can't affect the output — refactors,
logging, tests, or anything downstream of the LLM call.

## Validating a change

1. Run the eval on the current setup → `--experiment baseline` (records
   `<category>-<eval>-<timestamp>-baseline`).
2. Make your change in `kube-gen.yml` (prompt or model).
3. Re-run → `--experiment my-change`.
4. Compare the two experiments in Braintrust.

Each score needs to meet a minimum passing threshold to be considered
acceptable: 80% by default, or 100% for `pii_safe`, since any PII leak counts
as a failure. See the
[evaluation feedback loop](../architecture/llm-evaluations.md#evaluation-feedback-loop)
for what happens when a score falls below its threshold.

> Each **summary** `eval.py` defines a `_CRITERIA` string — a short rubric that
> tells the judge what to check for (e.g. "does it identify the root cause and
> affected systems"). This is separate from the actual prompt: the eval already
> pulls the real prompt live from `kube-gen.yml`, but `_CRITERIA` is written by
> hand and does not update automatically. When you change what a prompt is
> asking for, update `_CRITERIA` too — otherwise the judge keeps scoring against
> a rubric that no longer matches the prompt's intent. (Correlation evals score
> deterministically plus one reasoning judge, so they have no `_CRITERIA`.)

## Creating an eval

A scaffolding skill (the `/create-eval` slash command) wires a new eval — summary or
correlation — to the production config and generates a starter golden dataset. Run it
and follow its prompts.

An eval is a directory under `matik/evals/<category>/<type>/` with three files:

- **`eval.py`** — a thin wrapper that calls the shared harness with this eval's
  config (which prompt, inputs, dataset, and judge criteria).
- **`golden_dataset.yml`** — the golden rows themselves (fictional, PII-free
  `expected`), kept separate from code so they read and review as plain data.
- **`seed_dataset.py`** — a thin loader that reads `golden_dataset.yml` and upserts it
  to Braintrust.

The scripts auto-discover any directory with an `eval.py` / `seed_dataset.py`, so a new
eval shows up in `run_eval.sh` / `seed_dataset.sh` with no further changes. After
scaffolding, review the generated golden data, then seed and run it.

## Notes

- **CI:** evals do not run in CI. Run them locally before merging a prompt change.

## Glossary

- **Golden dataset** — a fixed set of test cases with known, human-reviewed
  expected answers, used to check whether a prompt or model change makes
  output better or worse.
- **LLM judge** — a second LLM used to score another LLM's output, since
  there's often no simple rule for "correct."
- **Self-bias** — a judge's tendency to rate output from its own model family
  more favorably. Avoided here by always judging with a different model
  family than the generator.
- **[Braintrust](https://gai.airbnb.tools)** — the platform where eval and
  passive-monitoring runs are logged and compared.
- **Experiment** — one scored run of an eval in Braintrust, identified by a
  name like `<category>-<eval>-<timestamp>`.
