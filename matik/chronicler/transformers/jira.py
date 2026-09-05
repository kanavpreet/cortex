"""JIRA webhook transformer.

Maps Atlassian Jira webhook payloads onto Matik's `JiraBaseMessage` (for Scribe)
and `EnrichmentRequest` (for the Enricher's LLM-summarization pipeline).

Scope (V1):
    Accepted webhookEvent values:
        - jira:issue_created
        - jira:issue_updated
        - comment_created
        - comment_updated
    Accepted projects:
        - TCMR (issue key starts with "TCMR-")
    Out of scope (logged and dropped):
        - jira:issue_deleted  (Scribe has no deletion path today)
        - comment_deleted     (same)

Atlassian Jira webhooks have no signing/HMAC scheme, so this transformer
sets `webhook_secret_key = ""` to disable the consumer's HMAC validation
path. See [ADR 018](../../_infra/docs/decisions/013-chronicler-direct-kafka-python.md).
"""

from typing import Any

from chronicler.transformers.base import register_transformer
from common.models.enricher_messages import EnrichmentRequest
from common.models.jira_issue_record import JiraIssueRecord
from common.models.scribe_messages import JiraBaseMessage
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive
from common.utils.jira_utils import (
    CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK,
    CUSTOM_FIELDS_TCMR_RELATED_PLANNED_END_DATE,
    CUSTOM_FIELDS_TCMR_RELATED_PLANNED_START_DATE,
    CUSTOM_FIELDS_TCMR_RELATED_SERVICES,
    aggregate_comments_for_llm,
    get_first_non_empty_string_field,
    get_string_list_field,
)

# Webhook event types this transformer accepts.
_FULL_PIPELINE_EVENTS = frozenset(
    {
        "jira:issue_created",
        "jira:issue_updated",
        "comment_created",
        "comment_updated",
    }
)

# Project key prefix gate. Mirrors the historian's tcmr_jql.
_TCMR_KEY_PREFIX = "TCMR-"


class JiraTransformer:
    """Transforms Jira webhook payloads into Scribe and Enricher messages."""

    source_type: str = "jira"
    # Event type lives in the payload body (`webhookEvent`), not an HTTP header.
    # The consumer still reads a header value for event_type but the transformer
    # ignores it and derives the real event type from the payload below.
    event_type_header: str = ""
    # Empty key disables HMAC validation in the consumer — Atlassian doesn't
    # sign webhooks. See module docstring.
    webhook_secret_key: str = ""

    def validate_signature(
        self, body: bytes, headers: dict[str, str], secret: str
    ) -> bool:
        """Defensive stub. Should never be called because webhook_secret_key=''
        causes the consumer to skip signature validation entirely."""
        return False

    def should_process(self, event_type: str, payload: dict[str, Any]) -> bool:
        webhook_event = payload.get("webhookEvent", "")
        if webhook_event not in _FULL_PIPELINE_EVENTS:
            return False
        issue = payload.get("issue") or {}
        issue_key = issue.get("key") or ""
        return issue_key.startswith(_TCMR_KEY_PREFIX)

    def to_base_message(self, payload: dict[str, Any]) -> JiraBaseMessage:
        issue = payload.get("issue") or {}
        fields = issue.get("fields") or {}

        summary: str | None = fields.get("summary")
        status = fields.get("status") or {}
        status_name: str | None = (
            status.get("name") if isinstance(status, dict) else None
        )

        created_at = parse_timestamp_to_utc(fields.get("created")) or utc_now_naive()

        # TCMR custom fields. Use the helpers that the historian already trusts
        # so we evolve together with any custom-field renames.
        tcmr_related_git_pr_link = (
            get_first_non_empty_string_field(
                fields, CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK
            )
            or None
        )
        tcmr_related_services = get_string_list_field(
            fields, CUSTOM_FIELDS_TCMR_RELATED_SERVICES
        )
        tcmr_planned_start_date = self._first_date(
            fields, CUSTOM_FIELDS_TCMR_RELATED_PLANNED_START_DATE
        )
        tcmr_planned_end_date = self._first_date(
            fields, CUSTOM_FIELDS_TCMR_RELATED_PLANNED_END_DATE
        )

        record = JiraIssueRecord(
            issue_id=issue.get("id") or "",
            issue_key=issue.get("key") or "",
            ticket_type="tcmr",
            summary=summary,
            status_name=status_name,
            created_at=created_at,
            issue_summary=None,  # filled by Enricher via LLM pipeline
            issue_comments_summary=None,  # filled by Enricher
            summary_hash=None,
            comments_hash=None,
            tcmr_related_git_pr_link=tcmr_related_git_pr_link,
            tcmr_related_services=tcmr_related_services,
            tcmr_planned_start_date=tcmr_planned_start_date,
            tcmr_planned_end_date=tcmr_planned_end_date,
        )
        data = record.model_dump(mode="json", exclude={"id"})

        # Chronicler can't resolve Backstage services (no API access). Tell
        # Scribe to preserve whatever the historian already wrote.
        return JiraBaseMessage(
            data=data, update_services=False, entered_at=utc_now_naive()
        )

    def to_enrichment_request(
        self, payload: dict[str, Any], task_id: str
    ) -> EnrichmentRequest | None:
        issue = payload.get("issue") or {}
        fields = issue.get("fields") or {}
        issue_key = issue.get("key") or ""
        summary: str | None = fields.get("summary")
        aggregated_comments = _extract_aggregated_comments(payload, fields)

        content: dict[str, str] = {}
        if summary:
            content["issue_description"] = summary
        if aggregated_comments:
            content["aggregated_comments"] = aggregated_comments

        if not content:
            return None

        return EnrichmentRequest(
            source_type=self.source_type,
            producer="chronicler",
            task_id=task_id,
            entity_id={"issue_key": issue_key},
            content=content,
            entered_at=utc_now_naive(),
        )

    @staticmethod
    def _first_date(fields: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            raw = fields.get(key)
            if raw:
                parsed = parse_timestamp_to_utc(raw)
                if parsed:
                    return parsed
        return None


def _extract_aggregated_comments(
    payload: dict[str, Any], fields: dict[str, Any]
) -> str:
    """Pull comment bodies from the payload, preferring the full comments array.

    Atlassian's `comment_created` / `comment_updated` webhook includes the full
    `issue.fields.comment.comments` array. For older Jira versions or trimmed
    payloads, fall back to the singleton `payload.comment.body`. The historian's
    next crawl will overwrite with the canonical full aggregation.
    """
    comment_section = fields.get("comment")
    if isinstance(comment_section, dict):
        comments = comment_section.get("comments")
        if isinstance(comments, list) and comments:
            bodies = [c.get("body") for c in comments if isinstance(c, dict)]
            return aggregate_comments_for_llm(bodies)

    standalone = payload.get("comment")
    if isinstance(standalone, dict):
        body = standalone.get("body")
        if isinstance(body, str):
            return aggregate_comments_for_llm([body])

    return ""


register_transformer("jira", JiraTransformer())
