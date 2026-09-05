"""JIRA issue type definitions."""

from enum import StrEnum


class JiraIssueType(StrEnum):
    """Defines the types of JIRA issues tracked in the system."""

    TCMR = "tcmr"
    OPERATIONAL = "operational"
    PRODUCT = "product"


# All issue types as a list
ALL_JIRA_ISSUE_TYPES = [
    JiraIssueType.TCMR,
    JiraIssueType.OPERATIONAL,
    JiraIssueType.PRODUCT,
]
