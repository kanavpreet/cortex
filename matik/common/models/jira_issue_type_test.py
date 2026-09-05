"""Unit tests for JiraIssueType enum."""

import pytest

from common.models.jira_issue_type import ALL_JIRA_ISSUE_TYPES, JiraIssueType


class TestJiraIssueType:
    """Test suite for JiraIssueType enum."""

    def test_enum_values(self) -> None:
        """Test that enum has expected values."""
        assert JiraIssueType.TCMR.value == "tcmr"
        assert JiraIssueType.OPERATIONAL.value == "operational"
        assert JiraIssueType.PRODUCT.value == "product"

    def test_enum_members(self) -> None:
        """Test that enum has all expected members."""
        assert hasattr(JiraIssueType, "TCMR")
        assert hasattr(JiraIssueType, "OPERATIONAL")
        assert hasattr(JiraIssueType, "PRODUCT")

    def test_enum_count(self) -> None:
        """Test that enum has exactly 3 members."""
        assert len(list(JiraIssueType)) == 3

    def test_enum_iteration(self) -> None:
        """Test iterating over enum members."""
        members = list(JiraIssueType)
        assert JiraIssueType.TCMR in members
        assert JiraIssueType.OPERATIONAL in members
        assert JiraIssueType.PRODUCT in members

    def test_enum_string_conversion(self) -> None:
        """Test that enum members can be converted to strings."""
        assert str(JiraIssueType.TCMR) == "tcmr"
        assert JiraIssueType.TCMR.value == "tcmr"
        assert JiraIssueType.OPERATIONAL.value == "operational"
        assert JiraIssueType.PRODUCT.value == "product"

    def test_enum_comparison(self) -> None:
        """Test comparing enum members."""
        tcmr1 = JiraIssueType.TCMR
        tcmr2 = JiraIssueType.TCMR
        assert tcmr1 is tcmr2
        assert JiraIssueType.TCMR is not JiraIssueType.OPERATIONAL  # type: ignore[comparison-overlap]
        assert JiraIssueType.TCMR is not JiraIssueType.PRODUCT  # type: ignore[comparison-overlap]

    def test_enum_from_string(self) -> None:
        """Test creating enum from string value."""
        assert JiraIssueType("tcmr") == JiraIssueType.TCMR
        assert JiraIssueType("operational") == JiraIssueType.OPERATIONAL
        assert JiraIssueType("product") == JiraIssueType.PRODUCT

    def test_enum_invalid_value(self) -> None:
        """Test that invalid value raises ValueError."""
        with pytest.raises(ValueError):
            JiraIssueType("invalid")

    def test_all_jira_issue_types_list(self) -> None:
        """Test that ALL_JIRA_ISSUE_TYPES contains all enum members."""
        assert len(ALL_JIRA_ISSUE_TYPES) == 3
        assert JiraIssueType.TCMR in ALL_JIRA_ISSUE_TYPES
        assert JiraIssueType.OPERATIONAL in ALL_JIRA_ISSUE_TYPES
        assert JiraIssueType.PRODUCT in ALL_JIRA_ISSUE_TYPES

    def test_all_jira_issue_types_order(self) -> None:
        """Test the order of items in ALL_JIRA_ISSUE_TYPES."""
        assert ALL_JIRA_ISSUE_TYPES[0] == JiraIssueType.TCMR
        assert ALL_JIRA_ISSUE_TYPES[1] == JiraIssueType.OPERATIONAL
        assert ALL_JIRA_ISSUE_TYPES[2] == JiraIssueType.PRODUCT
