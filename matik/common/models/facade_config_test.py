"""Unit tests for FacadeConfig model."""

from common.models.facade_config import FacadeConfig


class TestFacadeConfig:
    """Test suite for FacadeConfig SQLModel."""

    def test_default_values(self) -> None:
        """Test creating FacadeConfig with all default values."""
        config = FacadeConfig()
        assert (
            config.base_url
            == "https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai"
        )
        assert config.resource_bucket == "prototype"
        assert config.default_model == "matik-sandbox-gpt-4o"
        assert config.api_version == "2024-12-01-preview"
        assert config.max_retries == 3
        assert config.backoff_delays == [10.0, 20.0, 40.0]
        assert config.iap_token is None

    def test_custom_values(self) -> None:
        """Test creating FacadeConfig with custom values."""
        config = FacadeConfig(
            base_url="https://custom-llm.example.com/api",
            resource_bucket="production",
            default_model="gpt-4o-mini",
            api_version="2025-01-01",
            max_retries=5,
            backoff_delays=[1.0, 2.0, 4.0, 8.0],
            iap_token="test-iap-token-123",
        )
        assert config.base_url == "https://custom-llm.example.com/api"
        assert config.resource_bucket == "production"
        assert config.default_model == "gpt-4o-mini"
        assert config.api_version == "2025-01-01"
        assert config.max_retries == 5
        assert config.backoff_delays == [1.0, 2.0, 4.0, 8.0]
        assert config.iap_token == "test-iap-token-123"

    def test_partial_custom_values(self) -> None:
        """Test creating FacadeConfig with partial custom values."""
        config = FacadeConfig(
            resource_bucket="production",
            default_model="gpt-4o",
            iap_token="my-token",
        )
        # Custom values
        assert config.resource_bucket == "production"
        assert config.default_model == "gpt-4o"
        assert config.iap_token == "my-token"
        # Default values
        assert (
            config.base_url
            == "https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai"
        )
        assert config.api_version == "2024-12-01-preview"
        assert config.max_retries == 3
        assert config.backoff_delays == [10.0, 20.0, 40.0]

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "base_url": "https://test.example.com",
            "resource_bucket": "test-bucket",
            "default_model": "test-model",
            "api_version": "2024-06-01",
            "max_retries": 2,
            "backoff_delays": [5.0, 10.0],
            "iap_token": "token-xyz",
        }
        config = FacadeConfig.model_validate(data)
        assert config.base_url == "https://test.example.com"
        assert config.resource_bucket == "test-bucket"
        assert config.default_model == "test-model"
        assert config.api_version == "2024-06-01"
        assert config.max_retries == 2
        assert config.backoff_delays == [5.0, 10.0]
        assert config.iap_token == "token-xyz"

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = FacadeConfig(
            resource_bucket="production",
            max_retries=5,
        )
        data = config.model_dump()
        assert data["resource_bucket"] == "production"
        assert data["max_retries"] == 5
        assert data["iap_token"] is None
        assert (
            data["base_url"]
            == "https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai"
        )

    def test_backoff_delays_empty_list(self) -> None:
        """Test FacadeConfig with empty backoff delays."""
        config = FacadeConfig(backoff_delays=[])
        assert config.backoff_delays == []

    def test_backoff_delays_single_value(self) -> None:
        """Test FacadeConfig with single backoff delay."""
        config = FacadeConfig(backoff_delays=[30.0])
        assert config.backoff_delays == [30.0]

    def test_mock_mode_default_false(self) -> None:
        """Test mock_mode defaults to False."""
        config = FacadeConfig()
        assert config.mock_mode is False

    def test_mock_mode_explicit_true(self) -> None:
        """Test mock_mode can be set to True."""
        config = FacadeConfig(mock_mode=True)
        assert config.mock_mode is True

    def test_mock_mode_string_true(self) -> None:
        """Test mock_mode coerces 'true' string to True."""
        config = FacadeConfig.model_validate({"mock_mode": "true"})
        assert config.mock_mode is True

    def test_mock_mode_string_false(self) -> None:
        """Test mock_mode coerces 'false' string to False."""
        config = FacadeConfig.model_validate({"mock_mode": "false"})
        assert config.mock_mode is False

    def test_mock_mode_empty_string(self) -> None:
        """Test mock_mode coerces empty string to False (envsubst behavior)."""
        config = FacadeConfig.model_validate({"mock_mode": ""})
        assert config.mock_mode is False

    def test_mock_mode_none(self) -> None:
        """Test mock_mode coerces None to False."""
        config = FacadeConfig.model_validate({"mock_mode": None})
        assert config.mock_mode is False

    def test_mock_mode_string_one(self) -> None:
        """Test mock_mode coerces '1' string to True."""
        config = FacadeConfig.model_validate({"mock_mode": "1"})
        assert config.mock_mode is True

    def test_mock_mode_string_yes(self) -> None:
        """Test mock_mode coerces 'yes' string to True."""
        config = FacadeConfig.model_validate({"mock_mode": "yes"})
        assert config.mock_mode is True

    def test_mock_mode_string_uppercase_true(self) -> None:
        """Test mock_mode coerces 'TRUE' string to True (case-insensitive)."""
        config = FacadeConfig.model_validate({"mock_mode": "TRUE"})
        assert config.mock_mode is True
