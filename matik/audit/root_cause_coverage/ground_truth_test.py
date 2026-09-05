"""Tests for ground-truth extraction and verification."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from audit.conftest import _FakeLLMClient, _RaisingLLMClient
from audit.root_cause_coverage.ground_truth import (
    build_source_text,
    extract_ground_truth,
    verify_ground_truth,
)
from common.clients.ghe_client import RawPullRequest
from common.models.root_cause_audit import GroundTruth


def _context(
    ghe_client: MagicMock | None = None, jira_client: MagicMock | None = None
) -> SimpleNamespace:
    """A minimal stand-in for RootCauseAuditContext -- verify_ground_truth's
    registered sources only ever read ``ghe_client``/``jira_client`` off it."""
    return SimpleNamespace(
        ghe_client=ghe_client or MagicMock(),
        jira_client=jira_client or MagicMock(),
    )


# ---------------------------------------------------------------------------
# build_source_text
# ---------------------------------------------------------------------------


def test_build_source_text_includes_summary_and_updates() -> None:
    text = build_source_text("the summary", ["first update", "second update"])
    assert "the summary" in text
    assert "first update" in text
    assert "second update" in text


def test_build_source_text_handles_missing_summary() -> None:
    text = build_source_text(None, ["only update"])
    assert "only update" in text


def test_build_source_text_handles_no_updates() -> None:
    text = build_source_text("only summary", [])
    assert text == "Incident summary:\nonly summary"


def test_build_source_text_includes_channel_summary_when_given() -> None:
    text = build_source_text(
        "the summary", ["an update"], "Slack thread mentions PR #42"
    )
    assert "Slack thread mentions PR #42" in text
    assert "the summary" in text
    assert "an update" in text


def test_build_source_text_omits_channel_summary_when_absent() -> None:
    text = build_source_text("only summary", [])
    assert "Slack channel summary" not in text


# ---------------------------------------------------------------------------
# extract_ground_truth
# ---------------------------------------------------------------------------


def test_extract_ground_truth_parses_valid_response() -> None:
    response = json.dumps(
        {
            "change_related": True,
            "ownership": "airbnb",
            "cited_identifier": "https://github.airbnb.biz/org/repo/pull/209",
            "quote_span": "The issue was traced to PR #209.",
            "reasoning": "Explicit citation in update message.",
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result == GroundTruth(
        change_related=True,
        ownership="airbnb",
        cited_identifier="https://github.airbnb.biz/org/repo/pull/209",
        quote_span="The issue was traced to PR #209.",
        reasoning="Explicit citation in update message.",
    )
    assert client.calls[0]["operation"] == "root_cause_audit_extraction"


def test_extract_ground_truth_strips_markdown_json_fence() -> None:
    """A model that wraps its JSON in a ```json fence shouldn't be treated as
    a parse failure."""
    response = (
        "```json\n"
        + json.dumps(
            {
                "change_related": True,
                "ownership": "airbnb",
                "cited_identifier": "https://github.airbnb.biz/org/repo/pull/322",
                "quote_span": "PR #322 merged, triggering a Terraform apply.",
                "reasoning": "Explicit citation.",
            }
        )
        + "\n```"
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-7818", "a system prompt")  # type: ignore[arg-type]

    assert result.cited_identifier == "https://github.airbnb.biz/org/repo/pull/322"
    assert result.reasoning == "Explicit citation."


def test_extract_ground_truth_abstains_when_no_identifier() -> None:
    response = json.dumps(
        {
            "change_related": True,
            "ownership": "vendor",
            "cited_identifier": None,
            "quote_span": None,
            "reasoning": "Vendor outage, no Airbnb change.",
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.cited_identifier is None
    assert result.ownership == "vendor"


def test_extract_ground_truth_treats_malformed_json_as_abstention() -> None:
    client = _FakeLLMClient("not json")

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.change_related is False
    assert result.cited_identifier is None
    assert result.ownership == "unknown"
    assert result.source_traceable is None


def test_extract_ground_truth_treats_llm_failure_as_abstention() -> None:
    result = extract_ground_truth(
        _RaisingLLMClient(),  # type: ignore[arg-type]
        "source text",
        "INC-1234",
        "a system prompt",
    )

    assert result.cited_identifier is None
    assert result.reasoning == "extraction failed"
    assert result.source_traceable is None


# ---------------------------------------------------------------------------
# extract_ground_truth: source_traceable
# ---------------------------------------------------------------------------


def test_extract_ground_truth_parses_source_traceable_true() -> None:
    """E.g. a vendor outage reflected on a public status page."""
    response = json.dumps(
        {
            "change_related": True,
            "ownership": "vendor",
            "cited_identifier": None,
            "quote_span": None,
            "reasoning": "vendor outage, status page confirms it",
            "source_traceable": True,
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.source_traceable is True


def test_extract_ground_truth_parses_source_traceable_false() -> None:
    """E.g. a manual, untracked change or a hardware failure."""
    response = json.dumps(
        {
            "change_related": True,
            "ownership": "airbnb",
            "cited_identifier": None,
            "quote_span": None,
            "reasoning": "manual change, not tracked anywhere",
            "source_traceable": False,
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.source_traceable is False


def test_extract_ground_truth_defaults_source_traceable_none_when_absent() -> None:
    response = json.dumps(
        {
            "change_related": False,
            "ownership": "unknown",
            "cited_identifier": None,
            "quote_span": None,
            "reasoning": "genuinely ambiguous",
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.source_traceable is None


def test_extract_ground_truth_treats_non_bool_source_traceable_as_none() -> None:
    """A malformed value (not a JSON bool) is treated the same as absent --
    left for a human to decide, never coerced into a guess."""
    response = json.dumps(
        {
            "change_related": False,
            "ownership": "unknown",
            "cited_identifier": None,
            "quote_span": None,
            "reasoning": "n/a",
            "source_traceable": "yes",
        }
    )
    client = _FakeLLMClient(response)

    result = extract_ground_truth(client, "source text", "INC-1234", "a system prompt")  # type: ignore[arg-type]

    assert result.source_traceable is None


# ---------------------------------------------------------------------------
# verify_ground_truth
# ---------------------------------------------------------------------------


def _ground_truth(
    cited_identifier: str | None, quote_span: str | None = None
) -> GroundTruth:
    return GroundTruth(
        change_related=True,
        ownership="airbnb",
        cited_identifier=cited_identifier,
        quote_span=quote_span,
        reasoning="",
    )


def test_verify_returns_none_when_extraction_abstained() -> None:
    result = verify_ground_truth(_ground_truth(None), _context())  # type: ignore[arg-type]
    assert result is None


def test_verify_resolves_pr_from_full_url() -> None:
    ghe_client = MagicMock()
    ghe_client.get_pull_request = AsyncMock(
        return_value=RawPullRequest(
            id=987654321,
            number=209,
            title=None,
            body=None,
            state="closed",
            locked=False,
            created_at=None,
            updated_at=None,
            closed_at=None,
            merged_at=None,
            base_ref=None,
        )
    )
    ground_truth = _ground_truth(
        "https://github.airbnb.biz/Airbnb-ITX/o11y-log-vector-transform/pull/209"
    )

    result = verify_ground_truth(ground_truth, _context(ghe_client=ghe_client))  # type: ignore[arg-type]

    assert result is not None
    assert result.entity_type == "github_pr"
    # entity_id is the opaque PR id (matches ReliabilityCorrelation.entity_id),
    # not the human-facing PR number used to look it up.
    assert result.entity_id == "987654321"
    assert result.org == "Airbnb-ITX"
    assert result.repo == "o11y-log-vector-transform"
    ghe_client.get_pull_request.assert_awaited_once_with(
        "Airbnb-ITX", "o11y-log-vector-transform", 209
    )


def test_verify_returns_none_when_pr_not_found() -> None:
    ghe_client = MagicMock()
    ghe_client.get_pull_request = AsyncMock(return_value=None)
    ground_truth = _ground_truth("https://github.airbnb.biz/org/repo/pull/999999")

    result = verify_ground_truth(ground_truth, _context(ghe_client=ghe_client))  # type: ignore[arg-type]

    assert result is None


def test_verify_returns_none_for_bare_pr_number_with_no_repo() -> None:
    """A bare "#209" with no org/repo can't be checked against anything."""
    ground_truth = _ground_truth("PR #209")

    result = verify_ground_truth(ground_truth, _context())  # type: ignore[arg-type]

    assert result is None


def test_verify_falls_back_to_quote_span_url_for_bare_citation() -> None:
    """The LLM sometimes echoes a markdown link's display text ("PR #322")
    into cited_identifier instead of the URL, even though quote_span -- the
    full sentence it was pulled from -- carries the complete URL.
    Verification should still succeed via that fallback."""
    ghe_client = MagicMock()
    ghe_client.get_pull_request = AsyncMock(
        return_value=RawPullRequest(
            id=120198,
            number=322,
            title=None,
            body=None,
            state="closed",
            locked=False,
            created_at=None,
            updated_at=None,
            closed_at=None,
            merged_at=None,
            base_ref=None,
        )
    )
    ground_truth = _ground_truth(
        "PR #322",
        quote_span=(
            "[PR #322](https://github.airbnb.biz/Airbnb-ITX/"
            "terraform-security-groups/pull/322) merged, triggering an apply."
        ),
    )

    result = verify_ground_truth(ground_truth, _context(ghe_client=ghe_client))  # type: ignore[arg-type]

    assert result is not None
    assert result.entity_type == "github_pr"
    assert result.entity_id == "120198"
    assert result.org == "Airbnb-ITX"
    assert result.repo == "terraform-security-groups"
    ghe_client.get_pull_request.assert_awaited_once_with(
        "Airbnb-ITX", "terraform-security-groups", 322
    )


def test_verify_ignores_quote_span_fallback_when_pr_number_differs() -> None:
    """The fallback must not silently verify a different PR than the one
    actually cited -- only trust quote_span's URL when its PR number matches
    cited_identifier's."""
    ground_truth = _ground_truth(
        "PR #322",
        quote_span=(
            "See https://github.airbnb.biz/Airbnb-ITX/other-repo/pull/999 "
            "for unrelated context."
        ),
    )

    result = verify_ground_truth(ground_truth, _context())  # type: ignore[arg-type]

    assert result is None


def test_verify_resolves_tcmr() -> None:
    jira_client = MagicMock()
    jira_client.get_issue.return_value = {"key": "TCMR-22430"}
    ground_truth = _ground_truth("TCMR-22430")

    result = verify_ground_truth(ground_truth, _context(jira_client=jira_client))  # type: ignore[arg-type]

    assert result is not None
    assert result.entity_type == "jira_tcmr"
    assert result.entity_id == "TCMR-22430"
    jira_client.get_issue.assert_called_once_with("TCMR-22430")


def test_verify_returns_none_when_tcmr_not_found() -> None:
    jira_client = MagicMock()
    jira_client.get_issue.return_value = None
    ground_truth = _ground_truth("TCMR-99999")

    result = verify_ground_truth(ground_truth, _context(jira_client=jira_client))  # type: ignore[arg-type]

    assert result is None


def test_verify_returns_none_when_jira_raises() -> None:
    jira_client = MagicMock()
    jira_client.get_issue.side_effect = RuntimeError("boom")
    ground_truth = _ground_truth("TCMR-22430")

    result = verify_ground_truth(ground_truth, _context(jira_client=jira_client))  # type: ignore[arg-type]

    assert result is None


def test_verify_returns_none_when_ghe_raises() -> None:
    ghe_client = MagicMock()
    ghe_client.get_pull_request = AsyncMock(side_effect=RuntimeError("boom"))
    ground_truth = _ground_truth("https://github.airbnb.biz/org/repo/pull/209")

    result = verify_ground_truth(ground_truth, _context(ghe_client=ghe_client))  # type: ignore[arg-type]

    assert result is None


def test_verify_returns_none_for_unparseable_identifier() -> None:
    ground_truth = _ground_truth("a direct commit with no PR")

    result = verify_ground_truth(ground_truth, _context())  # type: ignore[arg-type]

    assert result is None
