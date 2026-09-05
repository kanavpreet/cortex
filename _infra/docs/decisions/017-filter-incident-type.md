# Incident Type Filter

Date: 2026-02-27

Status: `approved`

Collaborators: @camille-bustamante

## Context

We currently ingest all incidents from Incident.io through the Historian service.
With the introduction of the Chronicler, Incident.io will also send all `incident.created` and `incident.updated` events via webhook.

At this time, our correlation engine is focused exclusively on BizTech-related incidents.


## Decision

We will introduce incident type filtering at the ingestion layer.

### Historian

The Historian will use the available `incident_type` filter when pulling incidents from Incident.io to restrict ingestion to BizTech-related incidents only.

### Chronicler

Incident.io webhooks do not support filtering by `incident_type` at the source. Therefore, the Chronicler will apply the same filtering logic upon receiving `incident.created` and `incident.updated` events.

Only incidents matching the approved BizTech incident types will proceed further into the processing and correlation pipeline.

### Why not ingest everything and filter downstream?

Filtering at the earliest point of entry provides several advantages:
- Reduces unnecessary processing
- Minimizes database writes
- Avoids LLM enrichment on irrelevant incidents
- Improves signal-to-noise ratio in correlation scoring
- Lowers infrastructure and compute cost

By constraining the dataset early, we improve both efficiency and correlation quality.

## Future Expansion

When we decide to support additional domains outside of BizTech:
- We will expand the allowed `incident_type` list in the Incident.io configuration.
- The ingestion filter will be updated to include the new incident types.
- No architectural changes will be required.

This keeps the system extensible while maintaining current focus and performance.

## Consequences

- Non-BizTech incidents will not be ingested or correlated until explicitly allowed.
- Expanding scope will require an intentional configuration update.
- Historical non-BizTech incidents will not exist in our system unless backfilled.
