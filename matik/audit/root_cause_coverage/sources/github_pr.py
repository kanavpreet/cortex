"""GitHub PR root-cause source.

Registered on import (see ``audit/root_cause_coverage/sources/__init__.py``).
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from audit.root_cause_coverage.sources.registry import (
    RootCauseSourceSpec,
    register_root_cause_source,
)
from common.clients.ghe_client import GHEClient
from common.constants import GHE_BASE_URL
from common.models.root_cause_audit import VerifiedEntity
from common.utils import log_utils

if TYPE_CHECKING:
    from audit.root_cause_coverage.pipeline import RootCauseAuditContext

logger = log_utils.get_logger(__name__)

# Matches a full GHE PR URL, e.g.
# "https://github.airbnb.biz/Airbnb-ITX/o11y-log-vector-transform/pull/209"
_PR_URL_RE = re.compile(r"github[\w.-]*/([\w.-]+)/([\w.-]+)/pull/(\d+)")
# Matches a bare "#1234" or "org/repo#1234" form, when no URL was quoted.
_PR_BARE_RE = re.compile(r"(?:([\w.-]+)/([\w.-]+))?#(\d+)")

ParsedPrReference = tuple[str, str, int]


def _parse_pr_reference(text: str) -> tuple[str | None, str | None, int] | None:
    """Extract (org, repo, pr_number) from a PR citation.

    Handles a full GHE URL (org/repo present) or a bare ``#1234`` /
    ``org/repo#1234`` form -- org/repo are ``None`` in the bare-number case,
    which means the citation can't be verified (no repo to check it against).
    """
    url_match = _PR_URL_RE.search(text)
    if url_match:
        org, repo, number = url_match.groups()
        return org, repo, int(number)

    bare_match = _PR_BARE_RE.search(text)
    if bare_match:
        org, repo, number = bare_match.groups()
        return org, repo, int(number)

    return None


def _parse(cited_identifier: str, quote_span: str | None) -> ParsedPrReference | None:
    pr_reference = _parse_pr_reference(cited_identifier)
    if pr_reference is None:
        return None

    org, repo, pr_number = pr_reference
    if org and repo:
        return org, repo, pr_number

    # cited_identifier came back as a bare "#322" with no org/repo --
    # quote_span is the full sentence it was pulled from, and can carry the
    # full URL even when the LLM only echoed the shorter display text into
    # cited_identifier. Only trust it if it cites the *same* PR number, not
    # just any PR.
    fallback = _parse_pr_reference(quote_span or "")
    fallback_org, fallback_repo, fallback_number = fallback or (None, None, 0)
    if fallback_org and fallback_repo and fallback_number == pr_number:
        return fallback_org, fallback_repo, fallback_number

    logger.info(
        "cited PR has no resolvable org/repo, parking for review",
        cited_identifier=cited_identifier,
    )
    return None


async def _verify_pr_async(
    ghe_client: GHEClient, org: str, repo: str, pr_number: int
) -> VerifiedEntity | None:
    pr = await ghe_client.get_pull_request(org, repo, pr_number)
    if pr is None:
        return None
    # entity_id must be the opaque GitHub PR id (RawPullRequest.id), not the
    # human-facing PR number -- that's what enigmatologist writes into
    # ReliabilityCorrelation.entity_id. Matching on pr_number here would look
    # correct but silently never match a real correlation row.
    return VerifiedEntity(
        entity_type="github_pr", entity_id=str(pr.id), org=org, repo=repo
    )


def _verify(
    context: RootCauseAuditContext, parsed: ParsedPrReference
) -> VerifiedEntity | None:
    org, repo, pr_number = parsed
    try:
        return asyncio.run(_verify_pr_async(context.ghe_client, org, repo, pr_number))
    except Exception:
        logger.exception(
            "PR verification failed", org=org, repo=repo, pr_number=pr_number
        )
        return None


def _build_links(
    entity_ids: list[str], context: RootCauseAuditContext
) -> dict[str, str]:
    """Batched: one DB lookup for every github_pr row in a run, keyed by the
    same opaque PR id ``entity_id`` already stores -- no live GHE API call.
    ``GHEPullRequest.pull_request_id`` is that same id (primary key), with
    org/repo/number denormalized inline for exactly this URL-construction use
    case (see ``GHEPRDAO.find_prs_with_repo_by_pull_request_ids``)."""
    try:
        pull_request_ids = [int(entity_id) for entity_id in entity_ids]
    except ValueError:
        logger.warning(
            "non-numeric github_pr entity_id, skipping link lookup",
            entity_ids=entity_ids,
        )
        return {}
    try:
        prs = context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids(
            pull_request_ids
        )
    except Exception:
        logger.exception(
            "failed to look up PR links", pull_request_ids=pull_request_ids
        )
        return {}
    return {
        str(item.pr.pull_request_id): (
            f"{GHE_BASE_URL}/{item.org}/{item.repo_name}"
            f"/pull/{item.pr.pull_request_number}"
        )
        for item in prs
    }


register_root_cause_source(
    RootCauseSourceSpec(
        entity_type="github_pr",
        parse=_parse,
        verify=_verify,
        correlation_engine_field="biztech_github",
        build_links=_build_links,
    )
)
