"""Unit tests for TelescopeConfig model."""

from common.constants import DEFAULT_OTEL_ENDPOINT, DEFAULT_TELESCOPE_TIMEOUT_SECONDS
from common.models.telescope_config import TelescopeConfig


class TestTelescopeConfig:
    """Test suite for TelescopeConfig Pydantic model."""

    def test_default_values(self) -> None:
        """Test creating TelescopeConfig with default values."""
        config = TelescopeConfig()
        assert config.enabled is False
        assert config.tenant_id is None
        assert config.push_interval_seconds == 60
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
        assert config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS
        assert config.service_name is None
        assert config.service_version is None
        assert config.environment is None

    def test_enabled_with_tenant(self) -> None:
        """Test creating enabled TelescopeConfig with tenant."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id="matik-production",
            push_interval_seconds=30,
        )
        assert config.enabled is True
        assert config.tenant_id == "matik-production"
        assert config.push_interval_seconds == 30

    def test_disabled_config(self) -> None:
        """Test creating disabled TelescopeConfig."""
        config = TelescopeConfig(enabled=False)
        assert config.enabled is False

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "enabled": True,
            "tenant_id": "test-tenant",
            "push_interval_seconds": 120,
            "otel_endpoint": "http://custom:4318",  # Base URL only
            "timeout_seconds": 45,
        }
        config = TelescopeConfig.model_validate(data)
        assert config.enabled is True
        assert config.tenant_id == "test-tenant"
        assert config.push_interval_seconds == 120
        assert config.otel_endpoint == "http://custom:4318"
        assert config.timeout_seconds == 45

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = TelescopeConfig(enabled=True, tenant_id="my-tenant")
        data = config.model_dump()
        assert data["enabled"] is True
        assert data["tenant_id"] == "my-tenant"
        assert data["push_interval_seconds"] == 60
        assert data["otel_endpoint"] == DEFAULT_OTEL_ENDPOINT
        assert data["timeout_seconds"] == DEFAULT_TELESCOPE_TIMEOUT_SECONDS

    def test_partial_config(self) -> None:
        """Test creating config with partial values."""
        config = TelescopeConfig(enabled=True)
        assert config.enabled is True
        assert config.tenant_id is None  # Uses default
        assert config.push_interval_seconds == 60  # Uses default
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT  # Uses default
        assert (
            config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS
        )  # Uses default

    def test_none_values_use_defaults(self) -> None:
        """Test that None values are converted to defaults.

        When environment variables are unset, envsubst replaces them with empty
        strings, which YAML parses as None. This test ensures those None values
        are converted to sensible defaults.
        """
        config = TelescopeConfig(
            enabled=None,  # Should become False
            tenant_id=None,
            push_interval_seconds=None,  # Should become 60
            otel_endpoint=None,  # Should become DEFAULT_OTEL_ENDPOINT
            timeout_seconds=None,  # Should become DEFAULT_TELESCOPE_TIMEOUT_SECONDS
        )
        assert config.enabled is False
        assert config.tenant_id is None
        assert config.push_interval_seconds == 60
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
        assert config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS

    def test_model_validate_with_none_values(self) -> None:
        """Test creating model from dict with None values."""
        data = {
            "enabled": None,
            "tenant_id": None,
            "push_interval_seconds": None,
            "otel_endpoint": None,
            "timeout_seconds": None,
        }
        config = TelescopeConfig.model_validate(data)
        assert config.enabled is False  # None converted to default
        assert config.tenant_id is None
        assert config.push_interval_seconds == 60  # None converted to default
        assert (
            config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
        )  # None converted to default
        assert config.timeout_seconds == DEFAULT_TELESCOPE_TIMEOUT_SECONDS

    def test_runtime_config_fields(self) -> None:
        """Test runtime configuration fields."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id="test",
            service_name="historian",
            service_version="1.0.0",
            environment="production",
        )
        assert config.service_name == "historian"
        assert config.service_version == "1.0.0"
        assert config.environment == "production"

    def test_custom_otel_endpoint(self) -> None:
        """Test custom OTEL endpoint for Kubernetes environments."""
        config = TelescopeConfig(
            enabled=True,
            tenant_id="matik-prod",
            otel_endpoint="http://otel-collector.monitoring:4318",  # Base URL only
        )
        assert config.otel_endpoint == "http://otel-collector.monitoring:4318"

    def test_unsubstituted_env_var_uses_default(self) -> None:
        """Test that unsubstituted env vars like ${VAR} use defaults.

        When environment variables are not set, the config substitution leaves
        them as literal ${VAR} strings. This test ensures those are converted
        to sensible defaults.
        """
        config = TelescopeConfig(
            enabled=True,
            tenant_id="matik-test",
            otel_endpoint="${TELESCOPE_OTEL_ENDPOINT}",  # Unsubstituted env var
        )
        assert config.otel_endpoint == DEFAULT_OTEL_ENDPOINT
