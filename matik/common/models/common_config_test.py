"""Unit tests for CommonConfig model."""

from common.models.common_config import CommonConfig


class TestCommonConfig:
    """Test suite for CommonConfig Pydantic model."""

    def test_default_values(self) -> None:
        """Test creating CommonConfig with default values."""
        config = CommonConfig()
        assert config.environment == "sandbox"
        assert config.log_level == "INFO"
        assert config.port == 8080

    def test_custom_values(self) -> None:
        """Test creating CommonConfig with custom values."""
        config = CommonConfig(environment="production", log_level="DEBUG", port=9000)
        assert config.environment == "production"
        assert config.log_level == "DEBUG"
        assert config.port == 9000

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {"environment": "staging", "log_level": "WARNING", "port": 3000}
        config = CommonConfig.model_validate(data)
        assert config.environment == "staging"
        assert config.log_level == "WARNING"
        assert config.port == 3000

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = CommonConfig(environment="test", log_level="ERROR")
        data = config.model_dump()
        assert data["environment"] == "test"
        assert data["log_level"] == "ERROR"
        assert data["port"] == 8080

    def test_partial_override(self) -> None:
        """Test that unspecified fields use defaults."""
        config = CommonConfig(environment="production")
        assert config.environment == "production"
        assert config.log_level == "INFO"  # Default
        assert config.port == 8080  # Default
