<!--
Release notes template for Matik.

Derived from the v0.1.0 MVP release notes. Used by the /release-notes command to
shape GitHub's auto-generated notes into this format. Placeholders in
{{double braces}} are filled by the command. Keep the prose voice: a short
narrative intro, then themed "Key Highlights" sub-sections grouped by area (not
raw PR titles). Omit any sub-section with no content for the release.

Conventions:
- Title:  Matik {{version}} — {{release_theme}}   (e.g. "MVP Release", "Enrichment & Correlation")
- Section dividers use the ⸻ glyph, matching prior releases.
- Add the "Upgrade Notes" block only when there is something an operator must do
  (migrations, config/secret changes, behavior changes). The v0.1.0 notes had
  none, so it is optional.
- Contributors must be real GitHub handles (`@login`) grouped with the PR
  numbers they authored, e.g. "authored by @alice (#123, #128) and @bob
  (#130)" — resolved via `gh pr view <N> --json author`, never a generic
  "multiple engineers" placeholder.
-->

Matik {{version}} — {{release_theme}}

{{intro_paragraph_what_this_release_marks}}

{{intro_paragraph_context_or_architecture}}

⸻

Upgrade Notes
<!-- OPTIONAL — include only if there is action required. Lead with it. Delete the whole block (and its divider) otherwise. -->
	•	Migrations: {{migration_summary}}
	•	Config / secrets: {{config_or_secret_changes}}
	•	Behavior changes: {{behavior_changes}}

⸻

Key Highlights

Core Platform
	•	{{change}}

⸻

Integrations
	•	{{change}}

⸻

API and Service Layer
	•	{{change}}

⸻

Infrastructure and Deployment
	•	{{change}}

⸻

Observability and Instrumentation
	•	{{change}}

⸻

Developer Experience
	•	{{change}}

⸻

Data Modeling and Processing
	•	{{change}}

⸻

Documentation and Tooling
	•	{{change}}

⸻

Known Issues
<!-- OPTIONAL — delete if none. -->
	•	{{known_issue}}

⸻

Contributors

This release was authored by {{github_login_and_prs_per_contributor}}.
{{optional_special_thanks}}

<!-- Full changelog: {{compare_url}} -->
