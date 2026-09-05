"""Tests for the RootCauseSourceSpec registry."""

from audit.root_cause_coverage.sources import (
    all_root_cause_sources,
    get_root_cause_source,
    register_root_cause_source,
)
from audit.root_cause_coverage.sources.registry import RootCauseSourceSpec


def test_github_pr_spec_self_registered() -> None:
    """Importing the package registers the github_pr spec (side-effect import)."""
    spec = get_root_cause_source("github_pr")
    assert spec is not None
    assert spec.entity_type == "github_pr"
    assert spec.correlation_engine_field == "biztech_github"
    assert spec.build_links is not None


def test_jira_tcmr_spec_self_registered() -> None:
    """Importing the package registers the jira_tcmr spec (side-effect import)."""
    spec = get_root_cause_source("jira_tcmr")
    assert spec is not None
    assert spec.entity_type == "jira_tcmr"
    assert spec.correlation_engine_field == "jira"
    assert spec.build_links is not None


def test_get_root_cause_source_returns_none_for_unregistered_type() -> None:
    assert get_root_cause_source("_not_a_real_source") is None


def test_all_root_cause_sources_includes_github_and_jira() -> None:
    types = {spec.entity_type for spec in all_root_cause_sources()}
    assert {"github_pr", "jira_tcmr"} <= types


def test_register_and_get_roundtrip() -> None:
    """register_root_cause_source / get_root_cause_source round-trips a spec
    by entity_type."""
    spec = RootCauseSourceSpec(
        entity_type="_test_source",
        parse=lambda cited_identifier, quote_span: None,
        verify=lambda context, parsed: None,
        correlation_engine_field="_test_field",
    )
    register_root_cause_source(spec)
    assert get_root_cause_source("_test_source") is spec
