"""Unit tests for MatikConfig model."""

from common.models.api_config import ApiConfig
from common.models.biztech_github_config import BiztechGitHubConfig
from common.models.common_config import CommonConfig
from common.models.enigmatologist_config import EnigmatologistConfig
from common.models.greenroom_config import GreenroomConfig
from common.models.incidentio_config import IncidentIOConfig
from common.models.jira_config import JiraConfig
from common.models.matik_config import MatikConfig
from common.models.mysql_config import MySQLConfig
from common.models.telescope_config import TelescopeConfig


class TestMatikConfig:
    """Test suite for MatikConfig Pydantic model."""

    def test_default_values(self) -> None:
        """Test creating MatikConfig with default values."""
        config = MatikConfig()
        assert isinstance(config.common, CommonConfig)
        assert config.mysql is None
        assert config.api is None
        assert config.incidentio is None
        assert config.jira is None
        assert config.biztech_github is None
        assert config.enigmatologist is None
        assert config.greenroom is None
        assert config.telescope is None

    def test_with_common_config(self) -> None:
        """Test MatikConfig with custom common config."""
        config = MatikConfig(
            common=CommonConfig(environment="production", log_level="WARNING")
        )
        assert config.common.environment == "production"
        assert config.common.log_level == "WARNING"

    def test_with_mysql_config(self) -> None:
        """Test MatikConfig with MySQL config."""
        config = MatikConfig(
            mysql=MySQLConfig(
                engine="mysql",
                endpoint="db.example.com",
                username="matik",
                database="matik_prod",
            )
        )
        assert config.mysql is not None
        assert config.mysql.endpoint == "db.example.com"
        assert config.mysql.database == "matik_prod"

    def test_with_api_config(self) -> None:
        """Test MatikConfig with API config."""
        config = MatikConfig(api=ApiConfig(api_endpoint="http://api:8080"))
        assert config.api is not None
        assert config.api.api_endpoint == "http://api:8080"

    def test_with_incidentio_config(self) -> None:
        """Test MatikConfig with Incident.io config."""
        config = MatikConfig(
            incidentio=IncidentIOConfig(
                api_key="test-key",
                root_cause_prompt="Test root cause prompt",
                description_prompt="Test description prompt",
            )
        )
        assert config.incidentio is not None
        assert config.incidentio.api_key == "test-key"

    def test_with_jira_config(self) -> None:
        """Test MatikConfig with JIRA config."""
        config = MatikConfig(
            jira=JiraConfig(
                base_url="https://jira.example.com",
                username="service",
                password="secret",
            )
        )
        assert config.jira is not None
        assert config.jira.base_url == "https://jira.example.com"

    def test_with_biztech_github_config(self) -> None:
        """Test MatikConfig with Biztech GitHub config."""
        config = MatikConfig(
            biztech_github=BiztechGitHubConfig(
                ghe_base_url="https://github.corp.com",
                ghe_app_client_id="client-id",
                ghe_app_installation_id="install-id",
                ghe_app_id="app-id",
            )
        )
        assert config.biztech_github is not None
        assert config.biztech_github.ghe_base_url == "https://github.corp.com"

    def test_with_greenroom_config(self) -> None:
        """Test MatikConfig with Greenroom config."""
        config = MatikConfig(greenroom=GreenroomConfig(api_token="token"))
        assert config.greenroom is not None
        assert config.greenroom.api_token == "token"

    def test_with_enigmatologist_config(self) -> None:
        """Test MatikConfig with Enigmatologist config."""
        config = MatikConfig(
            enigmatologist=EnigmatologistConfig(
                scribe_queue_url="",
                correlation_system_prompt="You are a bot.",
            )
        )
        assert config.enigmatologist is not None
        assert config.enigmatologist.correlation_system_prompt == "You are a bot."

    def test_with_telescope_config(self) -> None:
        """Test MatikConfig with Telescope config."""
        config = MatikConfig(telescope=TelescopeConfig(enabled=True, tenant_id="matik"))
        assert config.telescope is not None
        assert config.telescope.enabled is True
        assert config.telescope.tenant_id == "matik"

    def test_full_config(self) -> None:
        """Test MatikConfig with all sections populated."""
        config = MatikConfig(
            common=CommonConfig(environment="production"),
            mysql=MySQLConfig(
                engine="mysql",
                endpoint="db.prod.com",
                username="matik",
                database="matik",
            ),
            api=ApiConfig(api_endpoint="http://api:8080"),
            incidentio=IncidentIOConfig(
                api_key="key",
                root_cause_prompt="Test root cause prompt",
                description_prompt="Test description prompt",
            ),
            jira=JiraConfig(
                base_url="https://jira.com",
                username="user",
                password="pass",
            ),
            greenroom=GreenroomConfig(api_token="token"),
            telescope=TelescopeConfig(enabled=True),
        )
        assert config.common.environment == "production"
        assert config.mysql is not None
        assert config.api is not None
        assert config.incidentio is not None
        assert config.jira is not None
        assert config.greenroom is not None
        assert config.telescope is not None

    def test_model_validate_from_dict(self) -> None:
        """Test creating MatikConfig from nested dict."""
        data = {
            "common": {"environment": "staging", "log_level": "DEBUG"},
            "mysql": {
                "engine": "mysql",
                "endpoint": "localhost",
                "username": "root",
                "database": "test",
            },
            "incidentio": {
                "api_key": "api-key",
                "root_cause_prompt": "Test root cause prompt",
                "description_prompt": "Test description prompt",
            },
        }
        config = MatikConfig.model_validate(data)
        assert config.common.environment == "staging"
        assert config.mysql is not None
        assert config.mysql.endpoint == "localhost"
        assert config.incidentio is not None
        assert config.incidentio.api_key == "api-key"

    def test_model_dump(self) -> None:
        """Test serializing MatikConfig to dict."""
        config = MatikConfig(
            common=CommonConfig(environment="test"),
        )
        data = config.model_dump()
        assert data["common"]["environment"] == "test"
        assert data["mysql"] is None

    def test_extra_fields_allowed(self) -> None:
        """Test that extra fields are allowed (model_config extra=allow)."""
        config = MatikConfig(custom_field="custom_value")
        assert config.custom_field == "custom_value"  # type: ignore[attr-defined]
