"""GitHub utility functions for githubkit library."""

import re
from typing import Any
from urllib.parse import urlparse

import httpx
from githubkit.exception import RateLimitExceeded, RequestFailed


def is_github_rate_limit_error(
    err: Exception | None = None, resp: httpx.Response | Any | None = None
) -> bool:
    """
    Check if a GitHub API error is a rate limit error.

    Supports githubkit exceptions and httpx.Response objects.

    Args:
        err: Exception from the API call, if any. Handles:
            - githubkit's RateLimitExceeded exception
            - githubkit's RequestFailed with status 403/429
            - Any exception with rate limit message
        resp: HTTP response from the API call, if any. Handles:
            - httpx.Response with status_code attribute
            - Any object with status_code or status attribute

    Returns:
        True if the error is a rate limit error, False otherwise
    """
    # Check response status code
    if resp is not None:
        status_code = getattr(resp, "status_code", None) or getattr(
            resp, "status", None
        )
        if status_code in (403, 429):
            return True

    if err is None:
        return False

    # Check for githubkit RateLimitExceeded exception
    if isinstance(err, RateLimitExceeded):
        return True

    # Check for githubkit RequestFailed with rate limit status
    if isinstance(err, RequestFailed):
        response = getattr(err, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            if status_code in (403, 429):
                return True

    # Check for rate limit message in error string
    rate_limit_regex = re.compile(r"(?i)API rate limit of .* still exceeded")
    return bool(rate_limit_regex.search(str(err)))


# Separator between the title, description, and comment sections in the
# combined PR field. A rare multi-char token (not a single ";"/",") so that
# semicolons/commas in a PR title or body don't collide with the structural
# separators — which matters both for LLM delineation and for the content hash
# used in change-detection (a collision would skip a needed re-summarization).
GHE_PR_FIELD_SEPARATOR = ":::"


def build_ghe_pr_content(title: str, description: str, comments: list[str]) -> str:
    """Combine a PR's title, description, and conversation comments into the
    single text the LLM summarizes.

    Format: ``"<title>:::<description>:::comments:<c1>:::<c2>:::..."`` — the
    title and description are prepended, then the conversation thread under a
    ``comments:`` marker, all joined by :data:`GHE_PR_FIELD_SEPARATOR`.
    Empty/blank comment bodies are dropped.

    Args:
        title: PR title.
        description: PR body/description.
        comments: Conversation comment bodies, in chronological order.

    Returns:
        The combined, summarization-ready string.
    """
    sep = GHE_PR_FIELD_SEPARATOR
    joined_comments = sep.join(c.strip() for c in comments if c and c.strip())
    return f"{title}{sep}{description}{sep}comments:{joined_comments}"


def is_ingestible_pr_comment(
    author_login: str,
    body: str,
    ingestible_comment_bots: list[str],
    jenkins_review_summary_title: str,
) -> bool:
    """Whether a PR conversation comment carries signal worth ingesting.

    Keeps only comments authored by a configured bot:
    - ``spacelift-prod[bot]``: all comments (Terraform plan/apply actions).
    - ``jenkins-prod[bot]``: only the AirChat "Review Summary" comment (its first
      line contains ``jenkins_review_summary_title``); the companion 'Review
      Issues' and 'AirChat Review … flagged …' variants are dropped.

    Everything else (humans, bots not in ``ingestible_comment_bots``) is dropped.

    Args:
        author_login: The comment author's GitHub login (e.g. "jenkins-prod[bot]").
        body: The comment body.
        ingestible_comment_bots: Base logins (no '[bot]' suffix) allowed to be
            ingested (``config.ingestible_comment_bots``).
        jenkins_review_summary_title: Title substring that keeps a jenkins-prod
            comment (``config.jenkins_review_summary_title``).

    Returns:
        True if the comment should be folded into the PR summary.
    """
    base = author_login.removesuffix("[bot]")
    if base not in ingestible_comment_bots:
        return False
    if base == "jenkins-prod":
        first_line = body.split("\n", 1)[0].casefold()
        return jenkins_review_summary_title.casefold() in first_line
    return True


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    """
    Parse a GitHub PR URL and extract org, repo, and PR number.

    Handles URLs like:
        - https://github.airbnb.biz/Org/repo/pull/19
        - https://github.com/org/repo/pull/123

    Args:
        url: GitHub pull request URL

    Returns:
        Tuple of (org, repo, pr_number) or None if URL doesn't match
    """
    if not url or not url.strip():
        return None

    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None

    # Must be http or https
    if parsed.scheme not in ("http", "https"):
        return None

    # Split path into segments, filtering empty strings
    segments = [s for s in parsed.path.split("/") if s]

    # Expected: ["org", "repo", "pull", "number"]
    if len(segments) < 4:
        return None

    if segments[2] != "pull":
        return None

    org = segments[0]
    repo = segments[1]

    try:
        pr_number = int(segments[3])
    except (ValueError, IndexError):
        return None

    if pr_number <= 0:
        return None

    return (org, repo, pr_number)
