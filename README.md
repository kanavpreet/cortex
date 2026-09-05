# Matik

## Project Overview

Matik, from the tagalog root word "awtomatiko" meaning automate, is an AIOps platform that provides a unified data catalog for powering intelligence and automation in the reliability space.

Matik is where reliability engineers and tools come together.

## Useful Links

- [Project Docs](https://developers.a.musta.ch/docs/default/Component/matik)
- [C4 Diagram](https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712/edit?viewport_loc=1306%2C964%2C2384%2C2972%2CyJO_wFNQ_qPY&invitationId=inv_16bf17b0-a22f-4cdf-b908-9e90f06fc47c)
- [Dashboard to monitor Deployments](https://headlamp.a.musta.ch/projects/matik)
- [Spinaker Deployments](https://spinnaker.a.musta.ch/#/applications/matik/executions)
- [Prod Database Information](https://porter.a.musta.ch/mysql/clusters/biztech)

# cortex

Cotex is a reliability data catalog plus LLM-powered intelligence for technical operations. 

What it actually does

1. Collects — pulls in incidents (Incident.io), alerts (PagerDuty), tickets (JIRA), postmortems (Google Docs), AWS status events, service metadata (Backstage/Greenroom), infra/monitoring signals (CloudTrail, Grafana, Opensearch), and dev-pipeline events (Jenkins, Argo CD, Spinnaker) — normalizing them all into one data catalog.
2. Correlates — links related incidents/alerts together to surface relationships that aren't obvious when each source is siloed (e.g. "this alert and that incident are the same underlying issue").
3. Understands — runs LLMs over the cataloged data to generate summaries, analysis, and context on demand.
4. Acts — exposes that intelligence via an API and as MCP tools, so it can auto-triage or help resolve alerts/tickets rather than just report on them.

Three imp aspect for this
- Detection — cataloging events as they happen
- Prevention — reducing MTTD/MTTM (mean time to detect/mitigate)
- Resolution — automatically triaging/resolving alerts and tickets

So it's less "another monitoring dashboard" and more "the reliability data layer + LLM brain" sitting underneath ops tooling — the plumbing that turns scattered incident/alert/ticket data into something an LLM (or a human) can reason over.
