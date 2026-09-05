"""GitHub Enterprise pull request webhook transformer."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

from chronicler.transformers.base import register_transformer
from common.models.enricher_messages import EnrichmentRequest
from common.models.scribe_messages import GHEPRBaseMessage
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive
from common.utils.github_utils import build_ghe_pr_content

if TYPE_CHECKING:
    from common.clients.ghe_client import GHEClient

GITHUB_EVENT_HEADER = "X-GitHub-Event"
GITHUB_SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE_PREFIX = "sha256="


def _get_header_ci(headers: dict[str, str], key: str) -> str:
    """Case-insensitive header lookup. Yoyo lowercases header keys."""
    if key in headers:
        return headers[key]
    lower = key.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return ""


class GitHubPRTransformer:
    """Transforms GitHub Enterprise webhook payloads into Scribe and Enricher messages."""

    source_type: str = "ghe_pr"
    event_type_header: str = GITHUB_EVENT_HEADER
    webhook_secret_key: str = "github_webhook_secret"

    def __init__(self, ghe_client: GHEClient | None = None) -> None:
        """Initialize the transformer.

        Args:
            ghe_client: Optional read-only GHE client used to fetch the PR
                conversation thread at webhook time (payloads omit comments).
                When None, enrichment uses the PR description only and the
                historian crawl backfills comments on its next pass.
        """
        self._ghe_client = ghe_client

    def validate_signature(
        self, body: bytes, headers: dict[str, str], secret: str
    ) -> bool:
        """HMAC-SHA256 over the raw body, hex-encoded, prefixed with `sha256=`.

        Matches GHE's `X-Hub-Signature-256` scheme. Yoyo lowercases header keys
        before producing to Kafka, so we look up case-insensitively.
        """
        signature_header = _get_header_ci(headers, GITHUB_SIGNATURE_HEADER)
        if not signature_header.startswith(_SIGNATURE_PREFIX):
            return False
        expected = (
            _SIGNATURE_PREFIX
            + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(signature_header, expected)

    def should_process(self, event_type: str, payload: dict[str, Any]) -> bool:
        if event_type != "pull_request":
            return False
        action = payload.get("action")
        merged = payload.get("pull_request", {}).get("merged", False)
        return action == "closed" and merged is True

    def to_base_message(self, payload: dict[str, Any]) -> GHEPRBaseMessage:
        pr = payload["pull_request"]
        repo = payload["repository"]

        org = payload.get("organization") or {}
        github_org_id: int = org.get("id") or repo.get("owner", {}).get("id", 0)
        github_org_login: str = org.get("login") or repo.get("owner", {}).get(
            "login", ""
        )

        data: dict[str, Any] = {
            "pull_request_id": pr["id"],
            "pull_request_number": pr["number"],
            "org_id": github_org_id,
            "org_login": github_org_login,
            "repo_id": repo["id"],
            "repo_name": repo.get("name", ""),
            "title": pr["title"],
            "merged": pr["merged"],
            "state": pr["state"],
            "locked": pr.get("locked", False),
            "created_at": parse_timestamp_to_utc(pr.get("created_at")),
            "closed_at": parse_timestamp_to_utc(pr.get("closed_at")),
            "merged_at": parse_timestamp_to_utc(pr.get("merged_at")),
            "target_branch_name": pr.get("base", {}).get("ref", ""),
            "pull_request_summary": None,
        }

        return GHEPRBaseMessage(data=data, entered_at=utc_now_naive())

    def to_enrichment_request(
        self, payload: dict[str, Any], task_id: str
    ) -> EnrichmentRequest | None:
        pr = payload["pull_request"]
        repo = payload["repository"]

        title = pr.get("title") or ""
        description = pr.get("body") or ""
        # We summarize title + description + comments, so an empty body alone is
        # not a reason to skip: the title is always present on a real PR and the
        # comment thread is fetched later by augment_enrichment_content (which the
        # consumer only invokes when this returns a request). Skip only the
        # degenerate case where there is neither a title nor a description.
        if not title.strip() and not description.strip():
            return None

        # org_id is the GitHub organization ID.  GHE webhook payloads expose it
        # via two paths:
        #   1. payload["organization"]["id"] — present for organisation-scoped
        #      webhooks (the common case at Airbnb).
        #   2. payload["repository"]["owner"]["id"] — always present; equals the
        #      organisation ID when the repo is owned by an org.
        # We prefer the top-level "organization" key and fall back to owner.id.
        # The enrichment hash lookup keys on pull_request_id (the primary key);
        # org_id/repository_id are carried for context and echoed back.
        org = payload.get("organization") or {}
        org_id: int = org.get("id") or repo.get("owner", {}).get("id", 0)

        return EnrichmentRequest(
            source_type=self.source_type,
            producer="chronicler",
            task_id=task_id,
            entity_id={
                "org_id": org_id,
                "pull_request_id": pr["id"],
                "repository_id": repo["id"],
            },
            content={
                # Comment-less form: "<title>;<description>;comments:". The
                # async augmentation hook overwrites this with the full thread
                # once comments are fetched (webhook payloads omit them).
                "original_description": build_ghe_pr_content(title, description, []),
            },
            entered_at=utc_now_naive(),
        )

    async def augment_enrichment_content(
        self, payload: dict[str, Any]
    ) -> dict[str, str]:
        """Fetch the PR conversation thread to fold into the enrichment content.

        Webhook payloads do not carry comments, so they are fetched via the GHE
        API. Returns an empty dict when no client is configured (the consumer
        then proceeds with the description-only enrichment). Any fetch error
        propagates to the consumer, which logs it and proceeds without comments.

        Args:
            payload: Parsed webhook JSON payload.

        Returns:
            ``{"original_description": "<title>;<description>;comments:<thread>"}``
            — the combined field rebuilt with the fetched comments, which
            overwrites the comment-less form from ``to_enrichment_request``.
            ``{}`` when no client is configured or the owner/repo cannot be
            resolved (the consumer then keeps the comment-less form).
        """
        if self._ghe_client is None:
            return {}

        pr = payload["pull_request"]
        repo = payload["repository"]
        org = payload.get("organization") or {}
        owner = org.get("login") or repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")
        if not owner or not repo_name:
            return {}

        comment_bodies = await self._ghe_client.get_pr_comments(
            owner, repo_name, pr["number"]
        )
        return {
            "original_description": build_ghe_pr_content(
                pr.get("title") or "", pr.get("body") or "", comment_bodies
            )
        }


register_transformer("github", GitHubPRTransformer())
