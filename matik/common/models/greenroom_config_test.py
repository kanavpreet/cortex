"""Unit tests for GreenroomConfig model."""

from common.models.greenroom_config import GreenroomConfig


class TestGreenroomConfig:
    """Test suite for GreenroomConfig Pydantic model."""

    def test_valid_minimal(self) -> None:
        """Test creating GreenroomConfig with minimal required fields."""
        config = GreenroomConfig(api_token="test-token")
        assert config.api_token == "test-token"
        assert config.host == "http://greenroom-production.greenroom-production:7007"

    def test_valid_full(self) -> None:
        """Test creating GreenroomConfig with all fields."""
        config = GreenroomConfig(
            api_token="greenroom-bearer-token",
            host="https://backstage.example.com:7007",
        )
        assert config.api_token == "greenroom-bearer-token"
        assert config.host == "https://backstage.example.com:7007"

    def test_optional_fields(self) -> None:
        """Test that api_token and iap_token are optional."""
        # Both api_token and iap_token are optional now
        # (validation happens at runtime in client based on environment)
        config = GreenroomConfig()
        assert config.api_token is None
        assert config.iap_token is None
        assert config.host == "http://greenroom-production.greenroom-production:7007"

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "api_token": "secret-token",
            "host": "http://localhost:7007",
        }
        config = GreenroomConfig.model_validate(data)
        assert config.api_token == "secret-token"
        assert config.host == "http://localhost:7007"

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = GreenroomConfig(api_token="my-token")
        data = config.model_dump()
        assert data["api_token"] == "my-token"
        assert data["host"] == "http://greenroom-production.greenroom-production:7007"

    def test_custom_host(self) -> None:
        """Test creating config with custom host."""
        config = GreenroomConfig(
            api_token="token",
            host="http://greenroom-staging.greenroom-staging:7007",
        )
        assert config.host == "http://greenroom-staging.greenroom-staging:7007"

    def test_iap_token_for_local_development(self) -> None:
        """Test creating config with IAP token for local development."""
        config = GreenroomConfig(
            iap_token="iap-token-from-iap-auth",
            host="https://developers.a.musta.ch",
        )
        assert config.iap_token == "iap-token-from-iap-auth"
        assert config.host == "https://developers.a.musta.ch"
        assert config.api_token is None
