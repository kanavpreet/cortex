"""Unit tests for LLMTracingConfig model."""

from common.models.llm_tracing_config import LLMTracingConfig


class TestLLMTracingConfig:
    """Test suite for LLMTracingConfig Pydantic model."""

    def test_default_values(self) -> None:
        """Test creating LLMTracingConfig with default values."""
        config = LLMTracingConfig()
        assert config.enabled is False
        assert config.braintrust_project_id is None
        assert config.capture_content is True
        assert config.environment is None
        assert config.service_name is None

    def test_none_capture_content_defaults_true(self) -> None:
        """A None capture_content value (unset env var) becomes True."""
        config = LLMTracingConfig(capture_content=None)
        assert config.capture_content is True

    def test_capture_content_can_be_disabled(self) -> None:
        """capture_content is honored when explicitly set to False."""
        config = LLMTracingConfig(capture_content=False)
        assert config.capture_content is False

    def test_enabled_with_project(self) -> None:
        """Test creating enabled LLMTracingConfig with a Braintrust project ID."""
        config = LLMTracingConfig(
            enabled=True,
            braintrust_project_id="matik-production",
        )
        assert config.enabled is True
        assert config.braintrust_project_id == "matik-production"

    def test_none_enabled_uses_default(self) -> None:
        """Test that a None enabled value (unset env var) becomes False."""
        config = LLMTracingConfig(enabled=None)
        assert config.enabled is False

    def test_model_validate(self) -> None:
        """Test creating model from dict."""
        data = {"enabled": True, "braintrust_project_id": "test-project"}
        config = LLMTracingConfig.model_validate(data)
        assert config.enabled is True
        assert config.braintrust_project_id == "test-project"
