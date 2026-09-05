# Passive Monitoring Guide

This guide explains how passive monitoring works and how to extend it. For how
it fits alongside offline evals, see
[LLM Evaluations](../architecture/llm-evaluations.md).

## What it does

Passive monitoring scores real production LLM traffic on a schedule, using an
LLM judge since there's no known correct answer to compare against.

It covers two categories:

- **Summaries** — `description_summary`, `root_cause_summary`,
  `pull_request_summary`, `issue_summary`, `issue_comments_summary`. Scored on
  `faithfulness` and `pii_safe`.
- **Correlations** — `incident_correlation`. Scored on `reasoning_quality`.

> ⚠️ **Keep in sync with offline evals.** The `faithfulness`/`pii_safe`
> prompts in `summaries/scorer.py`, and the reasoning rubric in
> `correlations/incident_correlation.py`, are copied from `matik`'s offline
> eval scorers — they are not shared code. If you tune a rubric in one place,
> manually port the same change to the matching file in `matik`. Otherwise
> offline evals and passive monitoring will silently score against different
> definitions of "good."

## Prerequisites

- **Access to the [`ml_models` repo](https://git.musta.ch/airbnb/ml_models)**,
  with `ba` ([BigAir CLI](https://developers.a.musta.ch/docs/default/component/bigair/),
  via `bigair-client`) available.
- **Braintrust access**, requested the same way as for offline evals — via
  [Gandalf](https://gandalf-lite.airbnb.tools/permissions/business_tools/braintrust-ai/braintrust-ai).
  This is only needed for ad-hoc test runs, since those run under your own
  identity rather than `svc-matik`.

The scheduled runs themselves run as the `svc-matik` service account, which
already has its own Braintrust access provisioned — you don't need to set
that up yourself.

## Where the code lives

Passive monitoring runs in the `ml_models` repo, under
`ml_models/matik/evaluations/passive_monitoring/`:

- `_common.py` — fetches recent rows from Braintrust Logs.
- `judge_client.py` — builds the Bedrock/Facade judge clients via
  `llm-fusion-hub-client`.
- `summaries/`, `correlations/` — one BigAir workflow per category, with one
  step per operation or correlation type.

## Schedule

Both categories are registered as recurring BigAir service runs
(`summaries.ba.toml`, `correlations.ba.toml`), running as `svc-matik` on a
schedule. The lookback window (`hours` in each `.ba.toml`) is set to match
that schedule, so each run picks up exactly the traffic since the last one.

View the live schedule and run history in the
[BigAir recurring workflows page](https://mli.airbnb.tools/bigair/recurring?workflow=matik).

## How fetching works

Each LLM call is logged as two spans: an operation-named span used only for
tagging (its input and output are always empty), and a nested `llm`-type span
with the real content. `fetch_recent_rows` looks up the operation-named
span's `root_span_id`, then uses it to find the matching `llm`-type span.

## Adding a new summary operation

Add its name to `SUMMARY_OPERATIONS` in `summaries.py`. The scoring loop
handles any operation in that list automatically.

## Adding a new correlation type

1. Add a module (see `incident_correlation.py`) with its own `parse_output`
   and `make_scorer`.
2. Register a `CorrelationType` and add it to `CORRELATION_TYPES` in
   `correlations.py`.
3. Set its `default_judge_provider` to the opposite model family from its
   generator.

## Testing a change

```bash
ba run matik/evaluations/passive_monitoring/summaries/summaries.py -- --hours 1 --limit 1
ba run matik/evaluations/passive_monitoring/correlations/correlations.py -- --hours 1 --limit 1
```

## The `limit` argument

`limit = 0` (or unset) means unlimited. `limit = N` caps rows scored **per
operation**, not in total.

## Glossary

- **[BigAir](https://developers.a.musta.ch/docs/default/component/bigair/)**
  — Airbnb's workflow-orchestration platform. Passive monitoring runs as a
  scheduled BigAir service run.
- **Service run** — a BigAir workflow registered to run automatically on a
  schedule, under a specific service account, defined by a `.ba.toml` file.
- **`svc-matik`** — the service account passive monitoring runs as.
- **[Braintrust](https://gai.airbnb.tools)** — the platform where production
  LLM calls are logged, and where passive-monitoring scores are written.
- **Braintrust Logs** — the record of real production LLM calls in
  Braintrust, which passive monitoring reads from.
- **BTQL** — Braintrust's query language, used to fetch rows from Braintrust
  Logs.
- **Span** — one recorded step of an LLM call in Braintrust (e.g. a specific
  request/response). A trace is made up of multiple spans.
- **`root_span_id`** — the id linking every span in the same trace together.
- **LLM judge** — a second LLM used to score another LLM's output, since
  there's often no simple rule for "correct."
- **[Gandalf](https://gandalf-lite.airbnb.tools)** — Airbnb's access-request
  tool, used here to request Braintrust access.
