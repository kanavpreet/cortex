"""Tests for the github_pr root-cause source's link-building."""

from datetime import datetime
from unittest.mock import MagicMock

from audit.root_cause_coverage.sources.github_pr import _build_links
from common.daos.ghe_pr_dao import GHEPullRequestWithRepo
from common.models.ghe_pr import GHEPullRequest


def _pr_with_repo(
    pull_request_id: int, org: str, repo_name: str, pull_request_number: int
) -> GHEPullRequestWithRepo:
    return GHEPullRequestWithRepo(
        pr=GHEPullRequest(
            pull_request_id=pull_request_id,
            pull_request_number=pull_request_number,
            org_id=1,
            org_login=org,
            repo_id=1,
            repo_name=repo_name,
            merged=True,
            state="closed",
            locked=False,
            created_at=datetime(2026, 8, 1),
            target_branch_name="main",
        ),
        org=org,
        repo_name=repo_name,
    )


def test_build_links_maps_entity_id_to_pr_url() -> None:
    context = MagicMock()
    context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = [
        _pr_with_repo(120198, "Airbnb-ITX", "matik", 322)
    ]

    links = _build_links(["120198"], context)

    assert links == {"120198": "https://github.airbnb.biz/Airbnb-ITX/matik/pull/322"}
    context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.assert_called_once_with(
        [120198]
    )


def test_build_links_empty_for_no_ids() -> None:
    context = MagicMock()

    assert _build_links([], context) == {}


def test_build_links_returns_empty_on_non_numeric_id() -> None:
    context = MagicMock()

    assert _build_links(["not-a-number"], context) == {}
    context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.assert_not_called()


def test_build_links_returns_empty_on_dao_failure() -> None:
    context = MagicMock()
    context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.side_effect = (
        RuntimeError("db down")
    )

    assert _build_links(["120198"], context) == {}


def test_build_links_omits_ids_the_dao_did_not_return() -> None:
    """An id with no matching row (e.g. deleted PR) just doesn't get a link --
    the caller falls back to a blank url for that row, not an error."""
    context = MagicMock()
    context.ghe_pr_dao.find_prs_with_repo_by_pull_request_ids.return_value = []

    assert _build_links(["999"], context) == {}
