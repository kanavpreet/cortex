"""Jira TCMR root-cause source.

Registered on import (see ``audit/root_cause_coverage/sources/__init__.py``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from audit.root_cause_coverage.sources.registry import (
    RootCauseSourceSpec,
    register_root_cause_source,
)
from common.constants import JIRA_BROWSE_URL
from common.models.root_cause_audit import VerifiedEntity
from common.utils import log_utils

if TYPE_CHECKING:
    from audit.root_cause_coverage.pipeline import RootCauseAuditContext

logger = log_utils.get_logger(__name__)

_TCMR_RE = re.compile(r"\bTCMR-\d+\b")


def _parse(cited_identifier: str, quote_span: str | None) -> str | None:
    match = _TCMR_RE.search(cited_identifier)
    return match.group(0) if match else None


def _verify(context: RootCauseAuditContext, issue_key: str) -> VerifiedEntity | None:
    try:
        issue = context.jira_client.get_issue(issue_key)
    except Exception:
        logger.exception("TCMR verification failed", issue_key=issue_key)
        return None
    if issue is None:
        return None
    return VerifiedEntity(entity_type="jira_tcmr", entity_id=issue_key)


def _build_links(
    entity_ids: list[str], context: RootCauseAuditContext
) -> dict[str, str]:
    """The TCMR key is already the human-readable identifier, so the link is
    a plain string format -- no lookup needed, unlike github_pr."""
    return {entity_id: f"{JIRA_BROWSE_URL}/{entity_id}" for entity_id in entity_ids}


register_root_cause_source(
    RootCauseSourceSpec(
        entity_type="jira_tcmr",
        parse=_parse,
        verify=_verify,
        correlation_engine_field="jira",
        build_links=_build_links,
    )
)
