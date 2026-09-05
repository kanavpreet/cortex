"""GHE (GitHub Enterprise) pull request data source spec.

Registered on import (see ``common/datasources/__init__.py``). This is the
single declaration of GHE PR's write columns + Scribe routing; the model-derived
``BaseUpsertDAO`` and the generic Scribe handler read it instead of
hand-maintained per-source column lists and handler classes.
"""

from __future__ import annotations

from common.datasources.registry import DataSourceSpec, register_source
from common.models.ghe_pr import GHEPullRequest
from common.models.scribe_messages import GHEPREnrichmentMessage


def _build_dao(engine, metrics=None):  # type: ignore[no-untyped-def]
    """Construct the GHE PR DAO. Imported lazily to avoid an import cycle (the
    DAO imports this spec via ``get_source``)."""
    from common.daos.ghe_pr_dao import GHEPRDAO

    return GHEPRDAO(engine, metrics)


register_source(
    DataSourceSpec(
        source_type="ghe_pr",
        record_model=GHEPullRequest,
        # ``ghe_pull_requests`` is denormalized: the primary key is the
        # globally-unique GitHub ``pull_request_id`` and (org_id, repo_id,
        # pull_request_number) is a unique constraint. Excluding the PK and the
        # immutable identity keys from the update set keeps them stable on
        # conflict (a base write must not rewrite the row's identity).
        conflict_keys=["pull_request_id", "org_id", "repo_id"],
        # ``created_at`` is the GitHub creation timestamp — write-once, never
        # refreshed on re-write. Note ``deleted_at`` is deliberately NOT excluded:
        # it stays in the derived update set so a re-discovered PR (whose incoming
        # model carries deleted_at=None) is revived to NULL on conflict, matching
        # the former hand-written builder that forced deleted_at=None.
        # ``ghe_pr_enrichment_entered_at`` is owned by this same source's
        # enrichment write-group, not the base one — it isn't a field on
        # GHEPREnrichmentMessage, so llm_columns can't auto-exclude it; a base
        # upsert must never reset the enrichment guard timestamp.
        exclude_columns=["created_at", "ghe_pr_enrichment_entered_at"],
        dao_factory=_build_dao,
        enrichment_message_model=GHEPREnrichmentMessage,
        enrichment_key="pull_request_id",
        # GHE PR's enrichment hook needs nothing beyond the message, so the hook
        # runs on the message directly (the default, cheap path).
        enrichment_hook_target="message",
        # Preserve the historical "always write both LLM columns" semantics: an
        # enrichment update writes pull_request_summary/description_hash even when
        # None (blanks the column) rather than leaving them untouched.
        enrichment_overwrites_with_none=True,
        base_entered_at_column="ghe_pr_base_entered_at",
        enrichment_entered_at_column="ghe_pr_enrichment_entered_at",
    )
)
