"""Tests for GitHub PR transformer."""

import asyncio
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock

from chronicler.transformers.github_pr import GitHubPRTransformer
from common.utils.github_utils import build_ghe_pr_content


def _make_webhook_payload(
    merged: bool = True,
    action: str = "closed",
    body: str | None = "Fix the auth bug in login flow",
    include_org: bool = True,
) -> dict[str, object]:
    """Build a realistic GitHub webhook payload for a pull request event."""
    payload: dict[str, object] = {
        "action": action,
        "pull_request": {
            "id": 123456789,
            "number": 42,
            "title": "Fix auth bug",
            "merged": merged,
            "state": "closed",
            "locked": False,
            "created_at": "2026-04-01T10:00:00Z",
            "closed_at": "2026-04-02T14:30:00Z",
            "merged_at": "2026-04-02T14:30:00Z",
            "body": body,
            "base": {"ref": "main"},
        },
        "repository": {
            "id": 987654,
            "name": "matik",
            "full_name": "airbnb/matik",
            "owner": {"id": 11111, "login": "airbnb", "type": "Organization"},
        },
    }
    if include_org:
        payload["organization"] = {"id": 11111, "login": "airbnb"}
    return payload


class TestShouldProcess:
    def test_merged_pr_is_processed(self) -> None:
        t = GitHubPRTransformer()
        assert t.should_process("pull_request", _make_webhook_payload()) is True

    def test_non_merged_pr_is_filtered(self) -> None:
        t = GitHubPRTransformer()
        payload = _make_webhook_payload(merged=False)
        assert t.should_process("pull_request", payload) is False

    def test_opened_action_is_filtered(self) -> None:
        t = GitHubPRTransformer()
        payload = _make_webhook_payload(action="opened")
        assert t.should_process("pull_request", payload) is False

    def test_non_pr_event_is_filtered(self) -> None:
        t = GitHubPRTransformer()
        assert t.should_process("push", _make_webhook_payload()) is False

    def test_closed_not_merged_is_filtered(self) -> None:
        t = GitHubPRTransformer()
        payload = _make_webhook_payload(merged=False, action="closed")
        assert t.should_process("pull_request", payload) is False


class TestToBaseMessage:
    def test_base_message_has_correct_fields(self) -> None:
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload())
        assert msg.source_type == "ghe_pr"
        assert msg.message_type == "base"
        assert msg.data["pull_request_id"] == 123456789
        assert msg.data["pull_request_number"] == 42
        assert msg.data["title"] == "Fix auth bug"
        assert msg.data["merged"] is True
        assert msg.data["state"] == "closed"
        assert msg.data["locked"] is False
        assert msg.data["repo_id"] == 987654
        assert msg.data["repo_name"] == "matik"
        assert msg.data["org_id"] == 11111
        assert msg.data["org_login"] == "airbnb"
        assert msg.data["target_branch_name"] == "main"

    def test_data_carries_org_id_and_login(self) -> None:
        """The flat data dict carries the GHE org id and login inline, since
        org/repo identity is denormalized into ghe_pull_requests."""
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload())
        assert msg.data["org_id"] == 11111
        assert msg.data["org_login"] == "airbnb"

    def test_org_login_falls_back_to_owner(self) -> None:
        """Org login falls back to repository.owner.login when 'organization' absent."""
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload(include_org=False))
        assert msg.data["org_id"] == 11111
        assert msg.data["org_login"] == "airbnb"

    def test_sensitive_fields_are_stripped(self) -> None:
        """PR body must NOT appear in the base message data dict."""
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload(body="sensitive PII data"))
        assert "body" not in msg.data
        assert "description" not in msg.data
        assert msg.data["pull_request_summary"] is None

    def test_description_hash_not_set_by_chronicler(self) -> None:
        """Hash is computed by the enricher, not the chronicler."""
        body_text = "Fix the auth bug in login flow"
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload(body=body_text))
        assert msg.data.get("description_hash") is None

    def test_empty_body_produces_no_hash(self) -> None:
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload(body=""))
        assert msg.data.get("description_hash") is None

    def test_none_body_produces_no_hash(self) -> None:
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload(body=None))
        assert msg.data.get("description_hash") is None

    def test_timestamps_are_parsed(self) -> None:
        t = GitHubPRTransformer()
        msg = t.to_base_message(_make_webhook_payload())
        assert msg.data["created_at"] is not None
        assert msg.data["closed_at"] is not None
        assert msg.data["merged_at"] is not None


class TestToEnrichmentRequest:
    def test_enrichment_request_has_correct_fields(self) -> None:
        t = GitHubPRTransformer()
        req = t.to_enrichment_request(_make_webhook_payload(), "task-001")
        assert req is not None
        assert req.source_type == "ghe_pr"
        assert req.producer == "chronicler"
        assert req.task_id == "task-001"
        assert req.entity_id == {
            "org_id": 11111,
            "pull_request_id": 123456789,
            "repository_id": 987654,
        }
        assert req.content == {
            "original_description": build_ghe_pr_content(
                "Fix auth bug", "Fix the auth bug in login flow", []
            )
        }

    def test_org_id_falls_back_to_owner_when_organization_absent(self) -> None:
        """When the top-level 'organization' key is absent, use repository.owner.id."""
        t = GitHubPRTransformer()
        payload = _make_webhook_payload(include_org=False)
        req = t.to_enrichment_request(payload, "task-002")
        assert req is not None
        assert req.entity_id["org_id"] == 11111

    def test_empty_body_still_enriches_via_title(self) -> None:
        """An empty body must NOT skip enrichment — the title (and later the
        comment thread) are still summarized. Regression for PRs like
        'Update README.md' that have no body but do have comments."""
        t = GitHubPRTransformer()
        req = t.to_enrichment_request(_make_webhook_payload(body=""), "task-001")
        assert req is not None
        assert req.content == {
            "original_description": build_ghe_pr_content("Fix auth bug", "", [])
        }

    def test_none_body_still_enriches_via_title(self) -> None:
        t = GitHubPRTransformer()
        req = t.to_enrichment_request(_make_webhook_payload(body=None), "task-001")
        assert req is not None
        assert req.content == {
            "original_description": build_ghe_pr_content("Fix auth bug", "", [])
        }

    def test_no_title_and_no_body_returns_none(self) -> None:
        """Only the degenerate case (no title and no body) skips enrichment."""
        t = GitHubPRTransformer()
        payload = _make_webhook_payload(body="")
        payload["pull_request"]["title"] = ""  # type: ignore[index]
        req = t.to_enrichment_request(payload, "task-001")
        assert req is None


class TestAugmentEnrichmentContent:
    def test_no_client_returns_empty(self) -> None:
        """Without a GHE client, no comments are fetched."""
        t = GitHubPRTransformer()
        result = asyncio.run(t.augment_enrichment_content(_make_webhook_payload()))
        assert result == {}

    def test_fetches_and_folds_comments_into_field(self) -> None:
        """With a client, the PR thread is fetched and folded into the field."""
        client = MagicMock()
        client.get_pr_comments = AsyncMock(return_value=["Needs a test", "Approved"])
        t = GitHubPRTransformer(ghe_client=client)
        result = asyncio.run(t.augment_enrichment_content(_make_webhook_payload()))
        assert result == {
            "original_description": build_ghe_pr_content(
                "Fix auth bug",
                "Fix the auth bug in login flow",
                ["Needs a test", "Approved"],
            )
        }
        client.get_pr_comments.assert_awaited_once_with("airbnb", "matik", 42)

    def test_no_comments_still_returns_combined_field(self) -> None:
        """A PR with no comments yields the combined field with an empty thread."""
        client = MagicMock()
        client.get_pr_comments = AsyncMock(return_value=[])
        t = GitHubPRTransformer(ghe_client=client)
        result = asyncio.run(t.augment_enrichment_content(_make_webhook_payload()))
        assert result == {
            "original_description": build_ghe_pr_content(
                "Fix auth bug", "Fix the auth bug in login flow", []
            )
        }

    def test_owner_falls_back_to_repo_owner(self) -> None:
        """Owner is resolved from repository.owner.login when 'organization' absent."""
        client = MagicMock()
        client.get_pr_comments = AsyncMock(return_value=["hi"])
        t = GitHubPRTransformer(ghe_client=client)
        asyncio.run(
            t.augment_enrichment_content(_make_webhook_payload(include_org=False))
        )
        client.get_pr_comments.assert_awaited_once_with("airbnb", "matik", 42)


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestValidateSignature:
    def test_valid_signature(self) -> None:
        t = GitHubPRTransformer()
        body = b'{"action": "closed"}'
        secret = "shhhh"
        headers = {"x-hub-signature-256": _sign(body, secret)}
        assert t.validate_signature(body, headers, secret) is True

    def test_signature_canonical_case_header(self) -> None:
        t = GitHubPRTransformer()
        body = b'{"action": "closed"}'
        secret = "shhhh"
        headers = {"X-Hub-Signature-256": _sign(body, secret)}
        assert t.validate_signature(body, headers, secret) is True

    def test_mismatched_secret_fails(self) -> None:
        t = GitHubPRTransformer()
        body = b'{"action": "closed"}'
        headers = {"x-hub-signature-256": _sign(body, "wrong-secret")}
        assert t.validate_signature(body, headers, "right-secret") is False

    def test_tampered_body_fails(self) -> None:
        t = GitHubPRTransformer()
        secret = "shhhh"
        sig_for_original = _sign(b'{"action": "closed"}', secret)
        headers = {"x-hub-signature-256": sig_for_original}
        assert t.validate_signature(b'{"action": "opened"}', headers, secret) is False

    def test_missing_signature_header_fails(self) -> None:
        t = GitHubPRTransformer()
        assert t.validate_signature(b"body", {}, "secret") is False

    def test_wrong_prefix_fails(self) -> None:
        t = GitHubPRTransformer()
        headers = {"x-hub-signature-256": "md5=something"}
        assert t.validate_signature(b"body", headers, "secret") is False
