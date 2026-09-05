# Adding a Root-Cause Source to the Audit

Use the `/add-root-cause-source skill` to add a new root-cause source type to the audit. The skill compares the correlation categories supported by enigmatologist with the sources the audit already understands. For any missing category, it scaffolds a new source spec for you.

## Prerequisite

Enigmatologist (the correlation engine) must already emit this source as a correlation candidate. Verify by checking:
- ServiceCorrelationResult and LLMCorrelationResult in `enigmatologist/correlations/reliability/incident/state.py`
- The category loop in parse_llm_correlations in `enigmatologist/correlations/reliability/incident/nodes.py`

If the source is not surfaced there yet, add it in enigmatologist first.

## How sources are registered

Each source type (for example, github_pr, jira_tcmr) is a RootCauseSourceSpec defined in its own file under audit/root_cause_coverage/sources/. Registration happens on import. A spec defines:

- `entity_type`: the audit’s canonical name for the source (for example, "github_pr").
- `parse(cited_identifier, quote_span)`: extracts and recognizes the source identifier from text; return None if it does not match.
- `verify(context, parsed)`: validates the identifier against the real system using a client from RootCauseAuditContext. Handle errors internally and return None on failure.
- `correlation_engine_field`: the field on enigmatologist’s correlation-result model that carries matches for this source (for example, "biztech_github").
- `build_links (optional)`: (entity_ids of this type, context) -> {entity_id: url}, batched, for the Correlations sheet's link column. Omit if the source has no linkable identifier.

Every source needs exactly one client to verify against (for example, a GitHub Enterprise or Jira client), added as a named field on RootCauseAuditContext and instantiated in main.py.

Relevance (relevant vs. noise) is decided deterministically by service overlap against the incident's own root-cause/affected services — see `classify_by_service_match` in `classification.py` — not by a source-specific prompt, so a new source needs no relevance-side changes at all.

## What you provide, the skill executes

The skill runs the whole process end to end but pauses for your input or confirmation at these two points:

- The identifier format and how to verify it — new information for each source that the skill can't infer from existing code, so it asks for it before writing the `parse`/`verify` functions itself.
- Approval of the prompt wording — the skill drafts the update to `ground_truth_extraction_prompt`, and you approve, edit, or reject it before it writes to kube-gen.yml. Same confirm-before-act gate the skill already uses before scaffolding the source itself.

## Future considerations
- `ground_truth_extraction_prompt` lists each source explicitly, so it grows as you add sources. We plan to make it source-agnostic after we have evaluation coverage for this audit.
