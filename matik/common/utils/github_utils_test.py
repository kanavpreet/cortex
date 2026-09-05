"""Unit tests for GitHub utility functions."""

from unittest.mock import MagicMock

import httpx
from githubkit.exception import RateLimitExceeded, RequestFailed

from common.utils.github_utils import is_github_rate_limit_error


class TestIsGitHubRateLimitError:
    """Test suite for is_github_rate_limit_error function."""

    def test_rate_limit_response_429(self) -> None:
        """Test detection of 429 status code."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        assert is_github_rate_limit_error(resp=resp) is True

    def test_non_rate_limit_response(self) -> None:
        """Test that non-rate-limit status codes return False."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        assert is_github_rate_limit_error(resp=resp) is False

        resp.status_code = 500
        assert is_github_rate_limit_error(resp=resp) is False

        resp.status_code = 404
        assert is_github_rate_limit_error(resp=resp) is False

    def test_rate_limit_response_403(self) -> None:
        """Test detection of 403 status code (GitHub secondary rate limit)."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403
        assert is_github_rate_limit_error(resp=resp) is True

    def test_rate_limit_error_message(self) -> None:
        """Test detection of rate limit error message."""
        err = Exception("API rate limit of 5000 requests per hour still exceeded")
        assert is_github_rate_limit_error(err=err) is True

    def test_rate_limit_error_message_case_insensitive(self) -> None:
        """Test that error message matching is case insensitive."""
        err = Exception("api RATE LIMIT of 100 still exceeded")
        assert is_github_rate_limit_error(err=err) is True

    def test_non_rate_limit_error_message(self) -> None:
        """Test that non-rate-limit errors return False."""
        err = Exception("Connection timeout")
        assert is_github_rate_limit_error(err=err) is False

        err = Exception("Authentication failed")
        assert is_github_rate_limit_error(err=err) is False

    def test_no_error_no_response(self) -> None:
        """Test that None values return False."""
        assert is_github_rate_limit_error() is False
        assert is_github_rate_limit_error(err=None, resp=None) is False

    def test_response_takes_precedence(self) -> None:
        """Test that 429 response is detected even with non-matching error."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        err = Exception("Some other error")
        assert is_github_rate_limit_error(err=err, resp=resp) is True

    def test_error_checked_when_response_not_429(self) -> None:
        """Test that error message is checked when response is not 429."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403
        err = Exception("API rate limit of 5000 still exceeded")
        # Response is checked first, and 403 triggers True
        assert is_github_rate_limit_error(err=err, resp=resp) is True

    def test_githubkit_rate_limit_exceeded(self) -> None:
        """Test detection of githubkit RateLimitExceeded exception."""
        # Create a mock that passes isinstance check
        err = MagicMock(spec=RateLimitExceeded)
        assert is_github_rate_limit_error(err=err) is True

    def test_githubkit_request_failed_403(self) -> None:
        """Test detection of githubkit RequestFailed with 403 status."""
        err = MagicMock(spec=RequestFailed)
        err.response = MagicMock()
        err.response.status_code = 403
        assert is_github_rate_limit_error(err=err) is True

    def test_githubkit_request_failed_429(self) -> None:
        """Test detection of githubkit RequestFailed with 429 status."""
        err = MagicMock(spec=RequestFailed)
        err.response = MagicMock()
        err.response.status_code = 429
        assert is_github_rate_limit_error(err=err) is True

    def test_githubkit_request_failed_other_status(self) -> None:
        """Test that githubkit RequestFailed with other status returns False."""
        err = MagicMock(spec=RequestFailed)
        err.response = MagicMock()
        err.response.status_code = 500
        assert is_github_rate_limit_error(err=err) is False

    def test_githubkit_request_failed_no_response(self) -> None:
        """Test githubkit RequestFailed with no response attribute."""
        err = MagicMock(spec=RequestFailed)
        err.response = None
        assert is_github_rate_limit_error(err=err) is False

    def test_response_with_status_attribute(self) -> None:
        """Test response object with 'status' attribute instead of 'status_code'."""
        resp = MagicMock()
        # Remove status_code, set status
        del resp.status_code
        resp.status = 429
        assert is_github_rate_limit_error(resp=resp) is True

    def test_response_with_status_attribute_403(self) -> None:
        """Test response object with 'status' attribute for 403."""
        resp = MagicMock()
        del resp.status_code
        resp.status = 403
        assert is_github_rate_limit_error(resp=resp) is True


class TestParsePrUrl:
    """Test suite for parse_pr_url function."""

    def test_github_enterprise_url(self) -> None:
        """Test parsing a GitHub Enterprise PR URL."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url(
            "https://github.airbnb.biz/Airbnb-ITX/infra_indexer/pull/19"
        )
        assert result == ("Airbnb-ITX", "infra_indexer", 19)

    def test_github_com_url(self) -> None:
        """Test parsing a github.com PR URL."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url("https://github.com/org/repo/pull/123")
        assert result == ("org", "repo", 123)

    def test_http_url(self) -> None:
        """Test parsing an HTTP (non-HTTPS) URL."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url("http://github.com/org/repo/pull/42")
        assert result == ("org", "repo", 42)

    def test_url_with_trailing_slash(self) -> None:
        """Test parsing URL with trailing slash."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url("https://github.com/org/repo/pull/5/")
        assert result == ("org", "repo", 5)

    def test_url_with_extra_path_segments(self) -> None:
        """Test parsing URL with extra path segments after PR number."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url("https://github.com/org/repo/pull/5/files")
        assert result == ("org", "repo", 5)

    def test_empty_string(self) -> None:
        """Test parsing empty string returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("") is None

    def test_whitespace_only(self) -> None:
        """Test parsing whitespace-only string returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("   ") is None

    def test_not_a_url(self) -> None:
        """Test parsing a non-URL string returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("not-a-url") is None

    def test_missing_pull_segment(self) -> None:
        """Test URL without /pull/ segment returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("https://github.com/org/repo/issues/123") is None

    def test_non_numeric_pr_number(self) -> None:
        """Test URL with non-numeric PR number returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("https://github.com/org/repo/pull/abc") is None

    def test_too_few_path_segments(self) -> None:
        """Test URL with too few path segments returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("https://github.com/org/pull") is None

    def test_zero_pr_number(self) -> None:
        """Test URL with zero PR number returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("https://github.com/org/repo/pull/0") is None

    def test_negative_pr_number(self) -> None:
        """Test URL with negative PR number returns None."""
        from common.utils.github_utils import parse_pr_url

        assert parse_pr_url("https://github.com/org/repo/pull/-1") is None

    def test_whitespace_padded_url(self) -> None:
        """Test URL with leading/trailing whitespace is handled."""
        from common.utils.github_utils import parse_pr_url

        result = parse_pr_url("  https://github.com/org/repo/pull/7  ")
        assert result == ("org", "repo", 7)


class TestBuildGhePrContent:
    """Test suite for build_ghe_pr_content."""

    def test_combines_title_description_and_comments(self) -> None:
        from common.utils.github_utils import build_ghe_pr_content

        result = build_ghe_pr_content(
            "Fix auth", "Fixes the login bug", ["LGTM", "add a test"]
        )
        assert result == "Fix auth:::Fixes the login bug:::comments:LGTM:::add a test"

    def test_no_comments_yields_empty_comments_section(self) -> None:
        from common.utils.github_utils import build_ghe_pr_content

        result = build_ghe_pr_content("Title", "Body", [])
        assert result == "Title:::Body:::comments:"

    def test_blank_comments_are_dropped_and_trimmed(self) -> None:
        from common.utils.github_utils import build_ghe_pr_content

        result = build_ghe_pr_content("T", "D", ["  keep me  ", "", "   ", "second"])
        assert result == "T:::D:::comments:keep me:::second"

    def test_empty_title_and_description(self) -> None:
        from common.utils.github_utils import build_ghe_pr_content

        assert build_ghe_pr_content("", "", []) == ":::" + ":::" + "comments:"


BOTS = ["jenkins-prod", "spacelift-prod"]
TITLE = "Review Summary"


def _keep(
    login: str,
    body: str,
    bots: list[str] | None = None,
    title: str = TITLE,
) -> bool:
    from common.utils.github_utils import is_ingestible_pr_comment

    return is_ingestible_pr_comment(login, body, BOTS if bots is None else bots, title)


class TestIsIngestiblePRComment:
    """Test suite for is_ingestible_pr_comment filtering."""

    def test_jenkins_review_summary_is_kept(self) -> None:
        body = "### Review Summary *(jenkins-airchat-review)*\n---\nLooks good."
        assert _keep("jenkins-prod[bot]", body) is True

    def test_jenkins_review_issues_is_dropped(self) -> None:
        body = "### Review Issues *(jenkins-airchat-review)*\n---\nIssue 1..."
        assert _keep("jenkins-prod[bot]", body) is False

    def test_jenkins_airchat_flagged_is_dropped(self) -> None:
        body = "### 🤖 AirChat Review *(jenkins-airchat-review)* flagged 6 issues 🤖"
        assert _keep("jenkins-prod[bot]", body) is False

    def test_spacelift_plan_is_kept(self) -> None:
        body = "### ⚠️ Action Required: This plan includes resource deletions"
        assert _keep("spacelift-prod[bot]", body) is True

    def test_spacelift_apply_is_kept(self) -> None:
        body = "### Spacelift update for foo-prod: apply succeeded."
        assert _keep("spacelift-prod[bot]", body) is True

    def test_human_comment_is_dropped(self) -> None:
        assert _keep("some-engineer", "LGTM, please merge") is False

    def test_other_bot_is_dropped(self) -> None:
        body = "### Review Summary"  # right title, wrong author
        assert _keep("other-bot[bot]", body) is False

    def test_bot_not_in_configured_list_is_dropped(self) -> None:
        # spacelift would normally be kept, but config only allows jenkins-prod.
        assert (
            _keep("spacelift-prod[bot]", "apply succeeded", bots=["jenkins-prod"])
            is False
        )

    def test_bare_login_without_bot_suffix_matches(self) -> None:
        assert _keep("spacelift-prod", "apply succeeded") is True
        assert _keep("jenkins-prod", "### Review Summary\nx") is True

    def test_jenkins_title_match_is_case_insensitive(self) -> None:
        assert _keep("jenkins-prod[bot]", "## REVIEW SUMMARY") is True

    def test_jenkins_title_marker_is_configurable(self) -> None:
        assert (
            _keep("jenkins-prod[bot]", "## Code Review Digest", title="Digest") is True
        )
        assert _keep("jenkins-prod[bot]", "## Review Summary", title="Digest") is False

    def test_jenkins_review_summary_only_matches_first_line(self) -> None:
        # "Review Summary" only in the body (not the title) is dropped.
        body = "### Review Issues\nSee the Review Summary above for context."
        assert _keep("jenkins-prod[bot]", body) is False
