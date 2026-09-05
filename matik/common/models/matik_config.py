"""Main Matik configuration container."""

from sqlmodel import Field, SQLModel

from common.models.api_config import ApiConfig
from common.models.artifactory_config import ArtifactoryConfig
from common.models.bedrock_config import BedrockConfig
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.chronicler_config import ChroniclerConfig
from common.models.common_config import CommonConfig
from common.models.enigmatologist_config import EnigmatologistConfig
from common.models.enricher_config import EnricherConfig
from common.models.facade_config import FacadeConfig
from common.models.greenroom_config import GreenroomConfig
from common.models.incidentio_config import IncidentIOConfig
from common.models.jira_config import JiraConfig
from common.models.llm_tracing_config import LLMTracingConfig
from common.models.mcp_config import McpConfig
from common.models.mysql_config import MySQLConfig
from common.models.root_cause_audit_config import RootCauseAuditConfig
from common.models.scribe_config import ScribeConfig
from common.models.sheets_config import SheetsConfig
from common.models.sqspurger_config import SqsPurgerConfig
from common.models.telescope_config import TelescopeConfig


class MatikConfig(SQLModel):
    """
    Main configuration class that loads from YAML and environment variables.

    Note: No table=True, this is a configuration model only.
    """

    model_config = {"extra": "allow"}

    common: CommonConfig = Field(default_factory=CommonConfig)
    artifactory: ArtifactoryConfig | None = Field(default=None)
    mysql: MySQLConfig | None = Field(default=None)
    api: ApiConfig | None = Field(default=None)
    incidentio: IncidentIOConfig | None = Field(default=None)
    jira: JiraConfig | None = Field(default=None)
    biztech_github: BiztechGitHubConfig | None = Field(default=None)
    enigmatologist: EnigmatologistConfig | None = Field(default=None)
    scribe: ScribeConfig | None = Field(default=None)
    bedrock: BedrockConfig | None = Field(default=None)
    facade: FacadeConfig | None = Field(default=None)
    greenroom: GreenroomConfig | None = Field(default=None)
    mcp: McpConfig | None = Field(default=None)
    telescope: TelescopeConfig | None = Field(default=None)
    llm_tracing: LLMTracingConfig | None = Field(default=None)
    enricher: EnricherConfig | None = Field(default=None)
    chronicler: ChroniclerConfig | None = Field(default=None)
    sqs_purger: SqsPurgerConfig | None = Field(default=None)
    sheets: SheetsConfig | None = Field(default=None)
    root_cause_audit: RootCauseAuditConfig | None = Field(default=None)
