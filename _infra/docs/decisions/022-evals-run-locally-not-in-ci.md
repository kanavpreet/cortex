# Evals Run Locally, Not in CI

Date: 2026-06-29

Status: `accepted`

Collaborators: @camille-bustamante

## Context

There is currently no paved path for running LLM evals in CI. The AI team is working on a solution — see the [LLM CI strategy doc](https://docs.google.com/document/d/1N9HIakqyZctVj1UtepwaTqr8leMgYUYO-kGg8pPPstc/edit) and [Slack thread](https://airbnb.slack.com/archives/C07FL8ERQUT/p1782254385459639). Until that lands, we run evals locally.

## Decision

Matik evals run **locally / on demand**, not automatically in CI on every commit. We run the relevant eval (via `run_eval.sh` or `uv run -m evals...`) before merging a change that could alter an LLM's output, and compare against a baseline in Braintrust. There is no eval CI job. See the [offline evals guide](../development/offline-evals.md) for how to run them.

## Consequences

- No automated gate catches prompt/model regressions — it relies on the author running the eval. The [offline evals guide](../development/offline-evals.md) documents when to.
- Revisit once the AI team ships a paved path for running evals in CI.
