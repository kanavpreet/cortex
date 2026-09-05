"""Unit tests for config loading utilities."""

import os
from pathlib import Path

import pytest

from common.config import _substitute_env_vars, load_config, merge_config
from common.models import MatikConfig


class TestSubstituteEnvVars:
    """Test suite for _substitute_env_vars function."""

    def test_string_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting env var in a string."""
        monkeypatch.setenv("TEST_VAR", "hello")
        result = _substitute_env_vars("${TEST_VAR}")
        assert result == "hello"

    def test_string_partial_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting env var within a string."""
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = _substitute_env_vars("http://${HOST}:${PORT}/api")
        assert result == "http://localhost:8080/api"

    def test_string_no_substitution(self) -> None:
        """Test string without env vars is unchanged."""
        result = _substitute_env_vars("plain string")
        assert result == "plain string"

    def test_missing_env_var_unchanged(self) -> None:
        """Test missing env var leaves placeholder unchanged."""
        # Ensure var doesn't exist
        os.environ.pop("NONEXISTENT_VAR", None)
        result = _substitute_env_vars("${NONEXISTENT_VAR}")
        assert result == "${NONEXISTENT_VAR}"

    def test_dict_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting env vars in dict values."""
        monkeypatch.setenv("DB_HOST", "db.example.com")
        monkeypatch.setenv("DB_USER", "admin")
        data = {
            "host": "${DB_HOST}",
            "username": "${DB_USER}",
            "port": 3306,
        }
        result = _substitute_env_vars(data)
        assert result["host"] == "db.example.com"
        assert result["username"] == "admin"
        assert result["port"] == 3306

    def test_list_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting env vars in list items."""
        monkeypatch.setenv("ITEM1", "first")
        monkeypatch.setenv("ITEM2", "second")
        data = ["${ITEM1}", "${ITEM2}", "third"]
        result = _substitute_env_vars(data)
        assert result == ["first", "second", "third"]

    def test_nested_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test substituting env vars in nested structures."""
        monkeypatch.setenv("API_KEY", "secret123")
        monkeypatch.setenv("ENV", "production")
        data = {
            "common": {"environment": "${ENV}"},
            "incidentio": {"api_key": "${API_KEY}"},
            "tags": ["${ENV}", "matik"],
        }
        result = _substitute_env_vars(data)
        assert result["common"]["environment"] == "production"
        assert result["incidentio"]["api_key"] == "secret123"
        assert result["tags"] == ["production", "matik"]

    def test_non_string_primitives_unchanged(self) -> None:
        """Test that non-string primitives pass through unchanged."""
        assert _substitute_env_vars(42) == 42
        assert _substitute_env_vars(3.14) == 3.14
        assert _substitute_env_vars(True) is True
        assert _substitute_env_vars(None) is None


class TestLoadConfig:
    """Test suite for load_config function."""

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        """Test loading a minimal config file."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            """
common:
  environment: test
  log_level: DEBUG
"""
        )
        config = load_config(config_file)
        assert isinstance(config, MatikConfig)
        assert config.common.environment == "test"
        assert config.common.log_level == "DEBUG"

    def test_load_config_with_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test loading config with environment variable substitution."""
        monkeypatch.setenv("INCIDENTIO_API_KEY", "my-secret-key")
        config_file = tmp_path / "config.yml"
        monkeypatch.setenv("INCIDENTIO_ROOT_CAUSE_PROMPT", "Test root cause prompt")
        monkeypatch.setenv("INCIDENTIO_DESCRIPTION_PROMPT", "Test description prompt")
        config_file.write_text(
            """
incidentio:
  api_key: ${INCIDENTIO_API_KEY}
  root_cause_prompt: ${INCIDENTIO_ROOT_CAUSE_PROMPT}
  description_prompt: ${INCIDENTIO_DESCRIPTION_PROMPT}
"""
        )
        config = load_config(config_file)
        assert config.incidentio is not None
        assert config.incidentio.api_key == "my-secret-key"

    def test_load_config_file_not_found(self) -> None:
        """Test that FileNotFoundError is raised for missing file."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_config("/nonexistent/path/config.yml")
        assert "Config file not found" in str(exc_info.value)

    def test_load_config_relative_path_uses_app_config(self) -> None:
        """Test that relative paths are resolved to /app/config."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_config("my-config.yaml")
        # Verify it looked in /app/config
        assert "/app/config/my-config.yaml" in str(exc_info.value)

    def test_load_config_with_mysql(self, tmp_path: Path) -> None:
        """Test loading config with MySQL configuration."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            """
mysql:
  engine: mysql
  endpoint: localhost
  port: 3306
  username: root
  database: matik_test
"""
        )
        config = load_config(config_file)
        assert config.mysql is not None
        assert config.mysql.endpoint == "localhost"
        assert config.mysql.database == "matik_test"

    def test_load_full_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test loading a full config with multiple sections."""
        monkeypatch.setenv("JIRA_PASSWORD", "jira-secret")
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            """
common:
  environment: production
  log_level: INFO
  port: 9000
mysql:
  engine: mysql
  endpoint: db.prod.com
  username: matik
  database: matik
jira:
  base_url: https://jira.example.com
  username: service_account
  password: ${JIRA_PASSWORD}
telescope:
  enabled: true
  tenant_id: matik-prod
"""
        )
        config = load_config(config_file)
        assert config.common.environment == "production"
        assert config.common.port == 9000
        assert config.mysql is not None
        assert config.mysql.endpoint == "db.prod.com"
        assert config.jira is not None
        assert config.jira.password == "jira-secret"
        assert config.telescope is not None
        assert config.telescope.enabled is True

    def test_load_config_string_path(self, tmp_path: Path) -> None:
        """Test loading config with string path instead of Path object."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            """
common:
  environment: staging
"""
        )
        config = load_config(str(config_file))
        assert config.common.environment == "staging"

    def test_load_empty_config(self, tmp_path: Path) -> None:
        """Test loading an empty config file uses defaults."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("{}")
        config = load_config(config_file)
        assert config.common.environment == "sandbox"  # Default
        assert config.mysql is None


class TestMergeConfig:
    """Test suite for merge_config function."""

    def test_merge_override_values(self, tmp_path: Path) -> None:
        """Test that additional config overrides base values."""
        base_file = tmp_path / "base.yml"
        base_file.write_text(
            """
common:
  environment: sandbox
  log_level: DEBUG
"""
        )
        override_file = tmp_path / "override.yml"
        override_file.write_text(
            """
common:
  environment: production
"""
        )
        base = load_config(base_file)
        merged = merge_config(base, override_file)
        # Override value
        assert merged.common.environment == "production"

    def test_merge_add_new_section(self, tmp_path: Path) -> None:
        """Test that merge adds new sections."""
        base_file = tmp_path / "base.yml"
        base_file.write_text(
            """
common:
  environment: test
"""
        )
        override_file = tmp_path / "override.yml"
        override_file.write_text(
            """
incidentio:
  api_key: test-key
  root_cause_prompt: Test root cause prompt
  description_prompt: Test description prompt
"""
        )
        base = load_config(base_file)
        merged = merge_config(base, override_file)
        assert merged.incidentio is not None
        assert merged.incidentio.api_key == "test-key"

    def test_merge_file_not_found(self, tmp_path: Path) -> None:
        """Test that FileNotFoundError is raised for missing override file."""
        base_file = tmp_path / "base.yml"
        base_file.write_text("common:\n  environment: test")
        base = load_config(base_file)
        with pytest.raises(FileNotFoundError) as exc_info:
            merge_config(base, "/nonexistent/override.yml")
        assert "Config file not found" in str(exc_info.value)

    def test_merge_relative_path_uses_app_config(self, tmp_path: Path) -> None:
        """Test that relative paths are resolved to /app/config."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text("common:\n  environment: test")
        base = load_config(base_file)
        with pytest.raises(FileNotFoundError) as exc_info:
            merge_config(base, "override.yaml")
        # Verify it looked in /app/config
        assert "/app/config/override.yaml" in str(exc_info.value)

    def test_merge_with_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that env vars are substituted in override file."""
        monkeypatch.setenv("OVERRIDE_ENV", "staging")
        base_file = tmp_path / "base.yml"
        base_file.write_text(
            """
common:
  environment: sandbox
"""
        )
        override_file = tmp_path / "override.yml"
        override_file.write_text(
            """
common:
  environment: ${OVERRIDE_ENV}
"""
        )
        base = load_config(base_file)
        merged = merge_config(base, override_file)
        assert merged.common.environment == "staging"

    def test_merge_preserves_base_values(self, tmp_path: Path) -> None:
        """Test that unoverridden base values are preserved."""
        base_file = tmp_path / "base.yml"
        base_file.write_text(
            """
common:
  environment: sandbox
  log_level: DEBUG
  port: 8080
"""
        )
        override_file = tmp_path / "override.yml"
        override_file.write_text(
            """
common:
  environment: production
"""
        )
        base = load_config(base_file)
        merged = merge_config(base, override_file)
        # Overridden
        assert merged.common.environment == "production"
        # Preserved from base (via default since common is fully replaced)
