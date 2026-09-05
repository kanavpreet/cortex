"""Unit tests for ApiConfig model."""

from common.models.api_config import ApiConfig


class TestApiConfig:
    """Test suite for ApiConfig Pydantic model."""

    def test_default_values(self) -> None:
        """Test creating ApiConfig with default values."""
        config = ApiConfig()
        assert config.api_endpoint == "http://matik-api:8080"

    def test_custom_endpoint(self) -> None:
        """Test creating ApiConfig with custom endpoint."""
        config = ApiConfig(api_endpoint="http://api.example.com:8080")
        assert config.api_endpoint == "http://api.example.com:8080"

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {"api_endpoint": "https://api.internal.com"}
        config = ApiConfig.model_validate(data)
        assert config.api_endpoint == "https://api.internal.com"

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = ApiConfig(api_endpoint="http://test:9000")
        data = config.model_dump()
        assert data["api_endpoint"] == "http://test:9000"
