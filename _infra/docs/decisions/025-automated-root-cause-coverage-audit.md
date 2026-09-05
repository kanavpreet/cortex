# Automated Root Cause Coverage Audit

Date: 2026-08-13

Status: `accepted`

Collaborators: @camille-bustamante

## Context

The [design doc](../architecture/automated-incident-audit.md) describes a daily pipeline that evaluates each newly completed incident against Matik correlations. It produces an auditable Google Sheet dataset for Root Cause Coverage, Relevant Context Coverage, and Noise Ratio, each measured at both investigation time and postmortem time.

The implementation (`audit/root_cause_coverage/`) follows the design. This ADR records several decisions made during implementation.

## Decision

### Fetch eligible incidents directly from incident.io (not the local DB)

Each run fetches eligible incidents directly from incident.io, rather than from the `incidentio_incidents` database table, because incident.io is the system of record for incident data.

### Ground-truth extraction aggregates multiple sources

Ground-truth extraction (`build_source_text`) pulls from more than one source, since a root-cause citation can appear in any of them. As of this decision that includes the incident.io summary, chronological incident updates from incident.io, and Matik’s OpsBot-derived Slack channel summary (`IncidentIOIncident.incident_channel_summary`, looked up per incident from the DB) — see `build_source_text` for the current, authoritative list, since more sources may be added over time.

In some cases, the citation appears only in Slack discussion and not in the incident.io summary. This brings a “Future considerations” item from the design doc into the initial implementation.

### Database access by environment

- Sandbox: reads from `matik_staging`
- Staging: reads from `matik_staging`
- Production: reads from `matik_production`

Sandbox has no real reliability correlation data, so both sandbox and staging read from staging’s database (a production replica). Because sandbox and staging share a database, they also share one output sheet: [“Matik Automated Audit - Development”](https://docs.google.com/spreadsheets/d/1ubO5lEca1gJh-_VJnWAzY4ycVhssVKtk-8ZRlUBW9LM/edit). Production writes to a separate sheet: [“Matik Automated Audit](https://docs.google.com/spreadsheets/d/18S7CB6V_CgVH7fhNVIMk166cJ-uanYZ5m2nMdHzCGL4/edit). This separation prevents development runs from mixing into the dataset consumed by downstream graphing tools.
