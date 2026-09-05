"""Unit tests for IncidentIOConfig model."""

import pytest
from pydantic import ValidationError

from common.models.incidentio_config import IncidentIOConfig

TEST_ROOT_CAUSE_PROMPT = "Test root cause prompt"
TEST_DESCRIPTION_PROMPT = "Test description prompt"


class TestIncidentIOConfig:
    """Test suite for IncidentIOConfig Pydantic model."""

    def test_valid_instantiation(self) -> None:
        """Test creating IncidentIOConfig with required fields."""
        config = IncidentIOConfig(
            api_key="test-api-key-12345",
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
        )
        assert config.api_key == "test-api-key-12345"

    def test_required_field_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            IncidentIOConfig()
        error_str = str(exc_info.value)
        assert "api_key" in error_str
        assert "root_cause_prompt" in error_str
        assert "description_prompt" in error_str

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {
            "api_key": "incident-io-secret-key",
            "root_cause_prompt": TEST_ROOT_CAUSE_PROMPT,
            "description_prompt": TEST_DESCRIPTION_PROMPT,
        }
        config = IncidentIOConfig.model_validate(data)
        assert config.api_key == "incident-io-secret-key"

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = IncidentIOConfig(
            api_key="my-secret-key",
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
        )
        data = config.model_dump()
        assert data["api_key"] == "my-secret-key"

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        config = IncidentIOConfig(
            api_key="test-key",
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
        )
        assert config.base_url == "https://api.incident.io"
        assert config.page_size == 100
        assert config.max_retries == 3
        assert config.timeout == 30.0
        assert config.backoff_delays == [2.0, 4.0, 8.0]
        assert config.start_date is None
        assert config.lookback_days == 30
        assert config.write_batch_size is None
        assert config.consumer_timeout_seconds is None

    def test_write_batch_size_configurable(self) -> None:
        """Test that write_batch_size can be configured."""
        config = IncidentIOConfig(
            api_key="test-key",
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
            write_batch_size=50,
        )
        assert config.write_batch_size == 50

    def test_consumer_timeout_seconds_configurable(self) -> None:
        """Test that consumer_timeout_seconds can be configured."""
        config = IncidentIOConfig(
            api_key="test-key",
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
            consumer_timeout_seconds=120,
        )
        assert config.consumer_timeout_seconds == 120

    def test_all_custom_values(self) -> None:
        """Test creating config with all custom values."""
        config = IncidentIOConfig(
            api_key="custom-key",
            base_url="https://custom.api.io",
            page_size=200,
            max_retries=5,
            timeout=60.0,
            backoff_delays=[1.0, 2.0],
            start_date="2024-01-01",
            lookback_days=7,
            write_batch_size=250,
            consumer_timeout_seconds=90,
            root_cause_prompt=TEST_ROOT_CAUSE_PROMPT,
            description_prompt=TEST_DESCRIPTION_PROMPT,
        )
        assert config.api_key == "custom-key"
        assert config.base_url == "https://custom.api.io"
        assert config.page_size == 200
        assert config.max_retries == 5
        assert config.timeout == 60.0
        assert config.backoff_delays == [1.0, 2.0]
        assert config.start_date == "2024-01-01"
        assert config.lookback_days == 7
        assert config.write_batch_size == 250
        assert config.consumer_timeout_seconds == 90
