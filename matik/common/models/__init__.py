"""Common data models for the Matik platform."""

from sqlmodel import SQLModel

from common.models.api_config import ApiConfig
from common.models.artifactory_config import ArtifactoryConfig
from common.models.bedrock_config import BedrockConfig
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.common_config import CommonConfig
from common.models.enricher_config import EnricherConfig, SourceMappingEntry
from common.models.enricher_messages import EnrichmentRequest
from common.models.facade_config import FacadeConfig
from common.models.ghe_org_crawl_tracker import GHEOrgCrawlTracker
from common.models.ghe_pr import GHEPullRequest
from common.models.ghe_pr_tracker import GHEPRTracker
from common.models.greenroom_config import GreenroomConfig
from common.models.greenroom_entity import (
    GreenroomEntity,
    GreenroomEntityRelation,
)
from common.models.incidentio_config import IncidentIOConfig
from common.models.incidentio_incident import IncidentIOIncident
from common.models.incidentio_tracker import IncidentIOTracker
from common.models.jira_batch_tracker import JiraBatchTracker
from common.models.jira_config import JiraConfig
from common.models.jira_issue import (
    Comment,
    Comments,
    Issue,
    IssueFields,
    IssueLink,
    IssueLinkType,
    Parent,
    Resolution,
    Status,
)
from common.models.jira_issue_record import (
    JiraIssueRecord,
)
from common.models.jira_issue_type import (
    ALL_JIRA_ISSUE_TYPES,
    JiraIssueType,
)
from common.models.matik_config import MatikConfig
from common.models.mcp_config import McpConfig
from common.models.mcp_tool_definition import McpToolDefinition
from common.models.mysql_config import MySQLConfig
from common.models.reliability_correlation import ReliabilityCorrelation
from common.models.reliability_correlation_group import (
    FeedbackEntry,
    ReliabilityCorrelationGroup,
)
from common.models.sqspurger_config import SqsPurgerConfig
from common.models.telescope_config import TelescopeConfig

metadata = SQLModel.metadata

__all__ = [
    "ALL_JIRA_ISSUE_TYPES",
    "ApiConfig",
    "ArtifactoryConfig",
    "BedrockConfig",
    "BiztechGitHubConfig",
    "Comment",
    "Comments",
    "CommonConfig",
    "EnricherConfig",
    "EnrichmentRequest",
    "FacadeConfig",
    "FeedbackEntry",
    "GHEOrgCrawlTracker",
    "GHEPRTracker",
    "GHEPullRequest",
    "GreenroomConfig",
    "GreenroomEntity",
    "GreenroomEntityRelation",
    "IncidentIOConfig",
    "IncidentIOIncident",
    "IncidentIOTracker",
    "Issue",
    "IssueFields",
    "IssueLink",
    "IssueLinkType",
    "JiraBatchTracker",
    "JiraConfig",
    "JiraIssueRecord",
    "JiraIssueType",
    "MatikConfig",
    "McpConfig",
    "McpToolDefinition",
    "MySQLConfig",
    "Parent",
    "ReliabilityCorrelation",
    "ReliabilityCorrelationGroup",
    "Resolution",
    "SourceMappingEntry",
    "SqsPurgerConfig",
    "Status",
    "TelescopeConfig",
]
