"""Unit tests for environment utilities."""

import os
from unittest.mock import patch

import pytest

from common.models.common_config import CommonConfig
from common.utils.env_utils import (
    determine_environment,
    extract_full_service,
    get_env_variable,
    is_local_environment,
)


class TestGetEnvVariable:
    """Test suite for get_env_variable function."""

    def test_returns_env_value_when_set(self) -> None:
        """Test returns environment variable value when set."""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            assert get_env_variable("TEST_VAR") == "test_value"

    def test_returns_default_when_not_set(self) -> None:
        """Test returns default value when variable not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert get_env_variable("MISSING_VAR", "default") == "default"

    def test_raises_error_when_not_set_no_default(self) -> None:
        """Test raises ValueError when variable not set and no default."""
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="TEST_VAR is not set"),
        ):
            get_env_variable("TEST_VAR")

    def test_returns_env_value_over_default(self) -> None:
        """Test env value takes precedence over default."""
        with patch.dict(os.environ, {"TEST_VAR": "env_value"}):
            assert get_env_variable("TEST_VAR", "default") == "env_value"

    def test_empty_string_uses_default(self) -> None:
        """Test empty string env value uses default."""
        with patch.dict(os.environ, {"TEST_VAR": ""}):
            assert get_env_variable("TEST_VAR", "default") == "default"

    def test_empty_string_raises_when_no_default(self) -> None:
        """Test empty string env value raises when no default."""
        with patch.dict(os.environ, {"TEST_VAR": ""}), pytest.raises(ValueError):
            get_env_variable("TEST_VAR")


class TestDetermineEnvironment:
    """Test suite for determine_environment function."""

    def test_production_variants(self) -> None:
        """Test production environment variants."""
        assert determine_environment("prod") == "production"
        assert determine_environment("production") == "production"
        assert determine_environment("main") == "production"
        assert determine_environment("master") == "production"

    def test_staging_variants(self) -> None:
        """Test staging environment variants."""
        assert determine_environment("staging") == "staging"
        assert determine_environment("stage") == "staging"

    def test_development_variants(self) -> None:
        """Test development environment variants."""
        assert determine_environment("dev") == "development"
        assert determine_environment("development") == "development"

    def test_case_insensitive(self) -> None:
        """Test environment detection is case insensitive."""
        assert determine_environment("PROD") == "production"
        assert determine_environment("Staging") == "staging"
        assert determine_environment("DEV") == "development"

    def test_unknown_returns_empty(self) -> None:
        """Test unknown environment returns empty string."""
        assert determine_environment("unknown") == ""
        assert determine_environment("test") == ""
        assert determine_environment("") == ""


class TestIsLocalEnvironment:
    """Test suite for is_local_environment function."""

    def test_none_config_returns_true(self) -> None:
        """Test returns True when config is None."""
        assert is_local_environment(None) is True

    def test_empty_environment_returns_true(self) -> None:
        """Test returns True when environment is empty string."""
        config = CommonConfig(environment="")
        assert is_local_environment(config) is True

    def test_local_environment_returns_true(self) -> None:
        """Test returns True when environment is 'local'."""
        config = CommonConfig(environment="local")
        assert is_local_environment(config) is True

    def test_production_environment_returns_false(self) -> None:
        """Test returns False when environment is production."""
        config = CommonConfig(environment="production")
        assert is_local_environment(config) is False

    def test_staging_environment_returns_false(self) -> None:
        """Test returns False when environment is staging."""
        config = CommonConfig(environment="staging")
        assert is_local_environment(config) is False

    def test_development_environment_returns_false(self) -> None:
        """Test returns False for development (non-local) environment."""
        config = CommonConfig(environment="development")
        assert is_local_environment(config) is False


class TestExtractFullService:
    """Test suite for extract_full_service function."""

    def test_historian_incidentio(self) -> None:
        """Test extraction for historian.incidentio.main."""
        assert (
            extract_full_service("historian.incidentio.main") == "historian-incidentio"
        )

    def test_historian_jira(self) -> None:
        """Test extraction for historian.jira.main."""
        assert extract_full_service("historian.jira.main") == "historian-jira"

    def test_api_main(self) -> None:
        """Test extraction for api.main (main as second part)."""
        assert extract_full_service("api.main") == "api"

    def test_chronicler_main(self) -> None:
        """Test extraction for chronicler.main."""
        assert extract_full_service("chronicler.main") == "chronicler"

    def test_single_part(self) -> None:
        """Test extraction for single component."""
        assert extract_full_service("historian") == "historian"

    def test_dunder_main(self) -> None:
        """Test returns unknown for __main__."""
        assert extract_full_service("__main__") == "unknown"

    def test_empty_string(self) -> None:
        """Test returns unknown for empty string."""
        assert extract_full_service("") == "unknown"

    def test_deeply_nested(self) -> None:
        """Test extraction for deeply nested module."""
        assert extract_full_service("api.routes.incidentio") == "api-routes"
