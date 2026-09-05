#Cortex
Cortex is a reliability data catalog plus LLM-powered intelligence for technical operations. 

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
