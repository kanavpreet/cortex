"""Tests for the Jira webhook transformer."""

from datetime import datetime
from typing import Any

import pytest

from chronicler.transformers.jira import JiraTransformer


def _issue_fields(
    summary: str | None = "Bump biztech-master proxysql pool",
    status_name: str | None = "Done",
    created: str | None = "2026-05-14T09:30:00.000+0000",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": summary,
        "status": {"name": status_name} if status_name else None,
        "created": created,
    }
    if extra:
        fields.update(extra)
    return fields


def _issue_payload(
    webhook_event: str = "jira:issue_updated",
    issue_key: str = "TCMR-9001",
    issue_id: str = "10042",
    fields: dict[str, Any] | None = None,
    comment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "webhookEvent": webhook_event,
        "timestamp": 1778600000000,
        "issue": {
            "id": issue_id,
            "key": issue_key,
            "fields": fields if fields is not None else _issue_fields(),
        },
    }
    if comment is not None:
        payload["comment"] = comment
    return payload


class TestShouldProcess:
    @pytest.mark.parametrize(
        "event",
        [
            "jira:issue_created",
            "jira:issue_updated",
            "comment_created",
            "comment_updated",
        ],
    )
    def test_accepted_event_with_tcmr_key(self, event: str) -> None:
        t = JiraTransformer()
        assert t.should_process("", _issue_payload(webhook_event=event)) is True

    def test_issue_deleted_is_filtered(self) -> None:
        t = JiraTransformer()
        payload = _issue_payload(webhook_event="jira:issue_deleted")
        assert t.should_process("", payload) is False

    def test_comment_deleted_is_filtered(self) -> None:
        t = JiraTransformer()
        payload = _issue_payload(webhook_event="comment_deleted")
        assert t.should_process("", payload) is False

    def test_unknown_event_is_filtered(self) -> None:
        t = JiraTransformer()
        assert (
            t.should_process("", _issue_payload(webhook_event="worklog_updated"))
            is False
        )

    def test_non_tcmr_key_is_filtered(self) -> None:
        t = JiraTransformer()
        payload = _issue_payload(issue_key="OPS-1234")
        assert t.should_process("", payload) is False

    def test_missing_issue_is_filtered(self) -> None:
        t = JiraTransformer()
        assert t.should_process("", {"webhookEvent": "jira:issue_updated"}) is False

    def test_missing_issue_key_is_filtered(self) -> None:
        t = JiraTransformer()
        payload = {"webhookEvent": "jira:issue_updated", "issue": {"id": "10042"}}
        assert t.should_process("", payload) is False

    def test_missing_webhook_event_is_filtered(self) -> None:
        t = JiraTransformer()
        payload = _issue_payload()
        del payload["webhookEvent"]
        assert t.should_process("", payload) is False


class TestToBaseMessage:
    def test_basic_field_mapping(self) -> None:
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload())
        assert msg.source_type == "jira"
        assert msg.message_type == "base"
        assert msg.update_services is False
        assert msg.data["issue_id"] == "10042"
        assert msg.data["issue_key"] == "TCMR-9001"
        assert msg.data["ticket_type"] == "tcmr"
        assert msg.data["summary"] == "Bump biztech-master proxysql pool"
        assert msg.data["status_name"] == "Done"
        assert msg.data["issue_summary"] is None  # filled by enricher
        assert msg.data["issue_comments_summary"] is None

    def test_summary_hash_is_none(self) -> None:
        """Hash is computed by the enricher, not the chronicler."""
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(summary="hi")))
        assert msg.data["summary_hash"] is None

    def test_summary_hash_is_none_when_summary_missing(self) -> None:
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(summary=None)))
        assert msg.data["summary_hash"] is None

    def test_created_falls_back_to_now_when_missing(self) -> None:
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(created=None)))
        assert msg.data["created_at"] is not None
        # Default fallback is utc_now_naive — just confirm it's a valid datetime string.
        datetime.fromisoformat(msg.data["created_at"].replace("Z", "+00:00"))

    def test_tcmr_custom_fields_extracted(self) -> None:
        t = JiraTransformer()
        extra = {
            "customfield_26927": "2026-05-13T10:00:00.000+0000",
            "customfield_26928": "2026-05-14T09:30:00.000+0000",
            "customfield_16509": "https://github.airbnb.biz/airbnb-ITX/foo/pull/123",
            "customfield_29146": ["matik-historian", "matik-chronicler"],
        }
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(extra=extra)))
        assert msg.data["tcmr_planned_start_date"] is not None
        assert msg.data["tcmr_planned_end_date"] is not None
        assert "github.airbnb.biz" in msg.data["tcmr_related_git_pr_link"]
        assert msg.data["tcmr_related_services"] == [
            "matik-historian",
            "matik-chronicler",
        ]

    def test_tcmr_fields_default_to_none(self) -> None:
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload())
        assert msg.data["tcmr_planned_start_date"] is None
        assert msg.data["tcmr_planned_end_date"] is None
        assert msg.data["tcmr_related_git_pr_link"] is None
        assert msg.data["tcmr_related_services"] is None

    def test_falls_back_planned_end_date_to_secondary_custom_field(self) -> None:
        t = JiraTransformer()
        extra = {"customfield_26926": "2026-05-14T09:30:00.000+0000"}
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(extra=extra)))
        assert msg.data["tcmr_planned_end_date"] is not None

    def test_comments_hash_is_none_when_array_present(self) -> None:
        """Hash is computed by the enricher, not the chronicler."""
        t = JiraTransformer()
        extra = {
            "comment": {
                "comments": [
                    {"body": "comment one"},
                    {"body": "comment two"},
                ]
            }
        }
        msg = t.to_base_message(_issue_payload(fields=_issue_fields(extra=extra)))
        assert msg.data["comments_hash"] is None

    def test_comments_hash_is_none_for_standalone_comment(self) -> None:
        """Hash is computed by the enricher, not the chronicler."""
        t = JiraTransformer()
        payload = _issue_payload(
            webhook_event="comment_created",
            comment={"body": "the new comment"},
        )
        msg = t.to_base_message(payload)
        assert msg.data["comments_hash"] is None

    def test_comments_hash_is_none_when_no_comments_in_payload(self) -> None:
        t = JiraTransformer()
        msg = t.to_base_message(_issue_payload())  # issue_updated, no comments
        assert msg.data["comments_hash"] is None

    def test_status_missing_does_not_raise(self) -> None:
        t = JiraTransformer()
        fields = _issue_fields(status_name=None)
        fields.pop("status")
        msg = t.to_base_message(_issue_payload(fields=fields))
        assert msg.data["status_name"] is None


class TestToEnrichmentRequest:
    def test_includes_summary_only_when_no_comments(self) -> None:
        t = JiraTransformer()
        req = t.to_enrichment_request(_issue_payload(), "task-1")
        assert req is not None
        assert req.source_type == "jira"
        assert req.producer == "chronicler"
        assert req.task_id == "task-1"
        assert req.entity_id == {"issue_key": "TCMR-9001"}
        assert req.content == {"issue_description": "Bump biztech-master proxysql pool"}

    def test_includes_both_keys_when_comments_present(self) -> None:
        t = JiraTransformer()
        payload = _issue_payload(
            webhook_event="comment_created",
            comment={"body": "the new comment"},
        )
        req = t.to_enrichment_request(payload, "task-1")
        assert req is not None
        assert req.content == {
            "issue_description": "Bump biztech-master proxysql pool",
            "aggregated_comments": "the new comment",
        }

    def test_includes_aggregated_comments_when_summary_missing(self) -> None:
        t = JiraTransformer()
        fields = _issue_fields(
            summary=None, extra={"comment": {"comments": [{"body": "only comment"}]}}
        )
        req = t.to_enrichment_request(_issue_payload(fields=fields), "task-1")
        assert req is not None
        assert req.content == {"aggregated_comments": "only comment"}

    def test_returns_none_when_nothing_to_enrich(self) -> None:
        t = JiraTransformer()
        fields = _issue_fields(summary=None)
        req = t.to_enrichment_request(_issue_payload(fields=fields), "task-1")
        assert req is None


class TestValidateSignature:
    def test_stub_always_returns_false(self) -> None:
        """Defensive stub: consumer never invokes this when webhook_secret_key=''."""
        t = JiraTransformer()
        assert t.validate_signature(b"body", {}, "secret") is False
