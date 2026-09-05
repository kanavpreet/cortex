"""Tests for the jira_tcmr root-cause source's link-building."""

from unittest.mock import MagicMock

from audit.root_cause_coverage.sources.jira_tcmr import _build_links


def test_build_links_formats_browse_url_per_entity_id() -> None:
    context = MagicMock()

    links = _build_links(["TCMR-22430", "TCMR-1"], context)

    assert links == {
        "TCMR-22430": "https://jira.airbnb.biz/browse/TCMR-22430",
        "TCMR-1": "https://jira.airbnb.biz/browse/TCMR-1",
    }


def test_build_links_empty_for_no_ids() -> None:
    context = MagicMock()

    assert _build_links([], context) == {}
