"""Unit tests for BiztechGitHubConfig model."""

import pytest
from pydantic import ValidationError

from common.models.biztech_github_config import BiztechGitHubConfig


class TestBiztechGitHubConfig:
    """Test suite for BiztechGitHubConfig Pydantic model."""

    def test_valid_minimal(self) -> None:
        """Test creating BiztechGitHubConfig with minimal required fields."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="Iv1.abc123",
            ghe_app_installation_id="12345",
            ghe_app_id="67890",
        )
        assert config.ghe_base_url == "https://github.example.com"
        assert config.ghe_api_path == "/api/v3"
        assert config.ghe_upload_path == "/api/uploads"
        assert config.ghe_api_url == "https://github.example.com/api/v3"
        assert config.ghe_upload_url == "https://github.example.com/api/uploads"
        assert config.ghe_app_client_id == "Iv1.abc123"
        assert config.ghe_app_installation_id == "12345"
        assert config.ghe_app_id == "67890"
        assert config.ghe_app_private_key is None
        assert config.ghe_app_private_key_file_name is None
        assert config.ghe_pr_summary_prompt is None
        assert config.facade_model == "gpt-4"  # Default

    def test_valid_full(self) -> None:
        """Test creating BiztechGitHubConfig with all fields."""
        config = BiztechGitHubConfig(
            ghe_app_private_key="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            ghe_app_private_key_file_name="matik-app.pem",
            ghe_base_url="https://github.corp.com",
            ghe_api_path="/api/v3",
            ghe_upload_path="/api/uploads",
            ghe_app_client_id="Iv1.xyz789",
            ghe_app_installation_id="99999",
            ghe_app_id="11111",
            ghe_pr_summary_prompt="Summarize this PR",
            facade_model="gpt-4-turbo",
        )
        assert config.ghe_app_private_key is not None
        assert "BEGIN RSA PRIVATE KEY" in config.ghe_app_private_key
        assert config.ghe_app_private_key_file_name == "matik-app.pem"
        assert config.ghe_pr_summary_prompt == "Summarize this PR"
        assert config.facade_model == "gpt-4-turbo"
        assert config.ghe_api_url == "https://github.corp.com/api/v3"
        assert config.ghe_upload_url == "https://github.corp.com/api/uploads"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            BiztechGitHubConfig()
        error_str = str(exc_info.value)
        assert "ghe_base_url" in error_str
        assert "ghe_app_client_id" in error_str
        assert "ghe_app_installation_id" in error_str
        assert "ghe_app_id" in error_str

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "ghe_base_url": "https://ghe.test.com",
            "ghe_app_client_id": "client-id",
            "ghe_app_installation_id": "install-id",
            "ghe_app_id": "app-id",
            "facade_model": "claude-3",
        }
        config = BiztechGitHubConfig.model_validate(data)
        assert config.ghe_base_url == "https://ghe.test.com"
        assert config.facade_model == "claude-3"
        assert config.ghe_api_url == "https://ghe.test.com/api/v3"

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
        )
        data = config.model_dump()
        assert data["ghe_base_url"] == "https://github.example.com"
        assert data["ghe_api_path"] == "/api/v3"
        assert data["ghe_upload_path"] == "/api/uploads"
        assert data["facade_model"] == "gpt-4"
        assert data["ghe_app_private_key"] is None

    def test_default_facade_model(self) -> None:
        """Test default facade model value."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
        )
        assert config.facade_model == "gpt-4"

    def test_int_coercion_to_string(self) -> None:
        """Test that integer values are coerced to strings.

        YAML parses unquoted numbers as integers, so the model must handle both.
        """
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id=12345,
            ghe_app_installation_id=53,
            ghe_app_id=51,
        )
        assert config.ghe_app_client_id == "12345"
        assert config.ghe_app_installation_id == "53"
        assert config.ghe_app_id == "51"

    def test_url_properties_with_trailing_slash(self) -> None:
        """Test that URL properties handle trailing slashes on base_url."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com/",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
        )
        assert config.ghe_api_url == "https://github.example.com/api/v3"
        assert config.ghe_upload_url == "https://github.example.com/api/uploads"

    def test_custom_paths(self) -> None:
        """Test that custom API and upload paths are used."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_api_path="/custom/api",
            ghe_upload_path="/custom/uploads",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
        )
        assert config.ghe_api_url == "https://github.example.com/custom/api"
        assert config.ghe_upload_url == "https://github.example.com/custom/uploads"

    def test_comment_filter_defaults(self) -> None:
        """ingestible_comment_bots / jenkins_review_summary_title default correctly."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
        )
        assert config.ingestible_comment_bots == ["jenkins-prod", "spacelift-prod"]
        assert config.jenkins_review_summary_title == "Review Summary"

    def test_ingestible_comment_bots_splits_csv_string(self) -> None:
        """A comma-separated string (from kube-gen) is parsed into a list."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
            ingestible_comment_bots="jenkins-prod, spacelift-prod ,foo-bot",
        )
        assert config.ingestible_comment_bots == [
            "jenkins-prod",
            "spacelift-prod",
            "foo-bot",
        ]

    def test_ingestible_comment_bots_accepts_list(self) -> None:
        """A YAML list is passed through unchanged."""
        config = BiztechGitHubConfig(
            ghe_base_url="https://github.example.com",
            ghe_app_client_id="client",
            ghe_app_installation_id="install",
            ghe_app_id="app",
            ingestible_comment_bots=["jenkins-prod"],
        )
        assert config.ingestible_comment_bots == ["jenkins-prod"]
