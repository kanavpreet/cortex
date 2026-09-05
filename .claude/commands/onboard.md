---
description: Onboard a new data source into the Matik ingestion pipeline end-to-end
argument-hint: <new source name, e.g. "pagerduty" or "google-docs post-mortems">
---

You onboard a **new data source** into the Matik ingestion pipeline (External API →
Historian/Chronicler → Scribe → MySQL, with the Enricher adding LLM summaries). You
generate the source-specific files and make the small edits to shared files, driving
the user through it and **asking clarifying questions whenever the repo can't answer
them**. `$ARGUMENTS` is the new source's name/description.

Read `_infra/docs/development/onboarding-a-data-source.md` first — it is the authoritative,
step-by-step reference (13 layers, the `DataSourceSpec` contract, NEW-file-vs-EDIT
per layer, and the copy-from templates). This command drives that doc; if the doc
and this command ever disagree, the doc wins — follow it and tell the user.

## Repo facts

- **Single wiring point:** `matik/common/datasources/registry.py` (`DataSourceSpec`)
  + the concrete specs `matik/common/datasources/{incidentio,jira,ghe_pr,incident_channel_summary}.py`.
  The registry self-populates via imports in `matik/common/datasources/__init__.py`.
- **Reference template:** Incident.io is the cleanest, fully spec-driven source —
  copy from it (`incidentio_incident.py`, `incidentio.py` spec,
  `incidentio_incident_dao.py`, `incidentio_client.py`, `historian/incidentio/*`,
  `api/routes/incidentio.py`). JIRA/GHE differ where the doc notes.
- **Inherited (do NOT reimplement):** `BaseUpsertDAO` (`common/daos/base_dao.py`),
  `GenericHandler` (`scribe/handlers/generic.py`), `run_historian` +
  `BaseCrawler` (`historian/base/`). A new source declares a spec + writes its
  source pieces; column lists/routing/handler are derived.
- **Conventions:** follow the model + timestamp guidelines in `CLAUDE.md` (all
  timestamps naive UTC via `parse_timestamp_to_utc`; JSON `list[str]` columns use
  `Column(JSON)` and are **never** `json.dumps`-ed by app code). The `CLAUDE.md`
  `## Data Sources` section lists existing sources + owners.

## Clarifying questions — ask BEFORE creating files

Gather these from the user (some you can infer from the API docs they point you at;
ask for the rest). Confirm your understanding back to them before scaffolding:

1. **`source_type`** — the short discriminator (e.g. `pagerduty`). And the config
   section name on `MatikConfig` — usually the same, but confirm (GHE's differs:
   `source_type="ghe_pr"` vs config `biztech_github`).
2. **API shape** — base URL, auth (API key? OAuth?), and pagination style (cursor?
   offset? page tokens?). This drives the client + the crawler's `_producer`.
3. **Unique / conflict keys** — which field(s) uniquely identify a record (→
   `conflict_keys` + the `UniqueConstraint`).
4. **Columns** — the record fields, which are `list[str]` JSON columns, and which
   are immutable-after-insert (→ `exclude_columns`).
5. **LLM enrichment** — which field(s) get LLM summaries, and from which source
   content (→ the enrichment message fields + the enricher `source_mappings` block).
   If none, the source is base-only (skip the enrichment message + enricher config).
6. **Tracker shape** — single-row incremental cursor (incidentio, the default) vs.
   windowed/per-partition backfill (JIRA). Pick the incidentio shape unless there's a
   reason not to.
7. **Realtime?** — does the source have webhooks (→ add a Chronicler transformer),
   or batch-only?
8. **Crawl shape** — does it fit the streaming producer/consumer model (paginate →
   queue → batch-write)? If yes, **subclass `BaseCrawler`** (recommended). If the
   crawl fans out or is synchronous/multi-job, **roll a custom `dispatch() ->
   CrawlerResult`** (like GHE/JIRA).

## Steps

Follow `_infra/docs/development/onboarding-a-data-source.md` in dependency order. For each, create the
NEW file or make the EDIT it specifies, copying from the Incident.io template and
adapting to the answers above. **Confirm names (source_type, file paths, table name)
with the user before writing files.**

1. **Models** — `common/models/<src>_record.py` (SQLModel `table=True`: JSON list
   cols, audit columns, `@field_validator` → `parse_timestamp_to_utc`,
   `UniqueConstraint`), `<src>_tracker.py`, `<src>_config.py`.
2. **Scribe messages** — add `<Src>BaseMessage` + `<Src>EnrichmentMessage(
   EnricherEnvelopeMixin)` to `common/models/scribe_messages.py`. Enrichment field
   names MUST equal the DB column names.
3. **Register models** — add record + tracker to `common/models/__init__.py`
   imports + `__all__` (required for Alembic autogenerate).
4. **Spec** — `common/datasources/<src>.py` (lazy `_build_dao` +
   `register_source(DataSourceSpec(...))`); add the import to
   `common/datasources/__init__.py`.
5. **DAO** — `common/daos/<src>_dao.py`: subclass `BaseUpsertDAO`, lazy `get_source`
   in `__init__`, only the finder(s) named by `spec.record_finder` + any hash
   lookups. Upsert/LLM-update are inherited.
6. **Client** — `common/clients/<src>_client.py`: `create_<src>_client(...)`, async
   httpx, shared retry/metrics/pagination.
7. **API tracker route** — `api/routes/<src>.py` (GET/POST tracker) + `include_router`
   in `api/main.py` + DAO dep in `api/routes/deps.py` (crawlers CRUD the tracker via
   the API, not the DB).
8. **Historian** — `historian/<src>/{__init__,__main__,main,<src>_crawler}.py`:
   `_build_and_run(ctx)` + `run_historian(...)`; crawler subclasses `BaseCrawler`
   (implement its 10 hooks) or rolls a custom `dispatch()`.
9. **Scribe** — add `"<src>": _build_generic("<src>")` to the handler dict in
   `scribe/main.py`, and `"<src>": _spec_routes("<src>")` to `VALID_ROUTES` in
   `scribe/processor.py`. Add a `HandlerHooks` only if the source needs a post-write
   side effect.
10. **Enricher** — add a `source_mappings.<src>` block to
    `local-configs/matik-enricher-config.yml` (config only). `input_keys` match the
    `EnrichmentRequest.content` keys; `output_field`/`hash_field` match the
    enrichment-message columns. Skip if base-only.
11. **Migration** — `migrator/alembic/versions/<hash>_*.py`: create the table (copy
    `616f7b7e3e44_initial_schema.py` + the audit-column DDL) and seed the single-row
    tracker (copy `e7795d51175b_insert_incidentio_tracker.py`). Chain `down_revision`
    to the current head.
12. **Config** — `local-configs/matik-historian-<src>-config.yml` (copy incidentio,
    `${VAR}` placeholders) + add `<src>: <Src>Config | None` to `MatikConfig`
    (`common/models/matik_config.py`).
13. **Chronicler (optional)** — `chronicler/transformers/<src>.py` (`ProviderTransformer`
    + `register_transformer(...)`) + the import in `chronicler/transformers/__init__.py`.
    Only if the source has webhooks.

## Verify

```bash
cd matik/
uv run --no-sync ruff check . && uv run --no-sync ruff format --check .
uv run --no-sync mypy .
uv run --no-sync pytest common/ historian/<src>/ scribe/ api/   # + chronicler/ if added
```

All must pass; fix issues before reporting done. (`--no-sync` avoids the artifactory
re-sync timeout; note some modules need optional deps that only install in CI.)

## Guardrails

- **Ask before creating files.** Confirm `source_type`, table name, and the file
  list with the user first. Don't invent config values, API endpoints, or column
  names — read them from the API docs the user provides, or ask.
- **Keep the source-specific pieces minimal** — inherit everything from the base
  (DAO upsert, Scribe handler, historian shell). The DAO writes only finders; Scribe
  gets only 2 one-line edits; the Enricher gets zero code.
- **Do not commit or push unless asked.** If asked to open a PR, its description
  **must** include a `## Test Plan` section (CI fails without it).
- If the source is base-only (no LLM), skip the enrichment message, `source_mappings`,
  and the enrichment publisher wiring — leave `enrichment_message_model=None`.
- If you changed `_infra/docs/development/onboarding-a-data-source.md` itself, register/refresh its
  entry in `_infra/portal.yml` under `Development:`.
