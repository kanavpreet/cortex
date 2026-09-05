"""Unit tests for EnigmatologistConfig model."""

import pytest

from common.models.enigmatologist_config import EnigmatologistConfig

_SCRIBE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/test-scribe-high"


class TestEnigmatologistConfig:
    """Test suite for EnigmatologistConfig Pydantic model."""

    def test_valid_instantiation_with_required_fields(self) -> None:
        """Test creating config with the required fields."""
        config = EnigmatologistConfig(
            scribe_queue_url=_SCRIBE_URL,
            correlation_system_prompt="You are a bot.",
            speculative_patterns=[],
        )
        assert config.correlation_system_prompt == "You are a bot."

    def test_scribe_queue_url_defaults_to_empty_string(self) -> None:
        """scribe_queue_url defaults to empty string until scribe triggers enigmatologist."""
        config = EnigmatologistConfig()
        assert config.scribe_queue_url == ""

    def test_api_use_case_no_prompt_needed(self) -> None:
        """Test that config can be created with only SQS fields (API use case)."""
        config = EnigmatologistConfig(scribe_queue_url="", speculative_patterns=[])
        assert config.correlation_system_prompt == ""
        assert config.sqs_queue_url == ""

    def test_default_values(self) -> None:
        """Test that all default values are set correctly."""
        config = EnigmatologistConfig(scribe_queue_url="", speculative_patterns=[])
        assert config.correlation_system_prompt == ""
        assert config.sqs_queue_url == ""
        assert config.scribe_queue_url == ""
        assert config.sqs_queue_region is None
        assert config.max_concurrent_correlations == 5
        assert config.sqs_max_messages == 10
        assert config.sqs_wait_time_seconds == 20
        assert config.sqs_visibility_timeout == 300
        assert config.sqs_poll_error_delay == 5
        assert config.matik_api_read_timeout == 10.0
        assert config.matik_api_write_timeout == 30.0
        assert config.lookback_hours_github == 6
        assert config.lookback_hours_jira == 24
        assert config.min_llm_score == 0.3
        assert config.speculative_patterns == []
        assert config.speculative_max_score == 0.7

    def test_all_custom_values(self) -> None:
        """Test creating config with all custom values."""
        config = EnigmatologistConfig(
            sqs_queue_url="https://sqs.us-west-2.amazonaws.com/123/my-queue",
            scribe_queue_url="https://sqs.us-west-2.amazonaws.com/123/scribe-high",
            sqs_queue_region="us-west-2",
            max_concurrent_correlations=10,
            sqs_max_messages=5,
            sqs_wait_time_seconds=10,
            sqs_visibility_timeout=600,
            sqs_poll_error_delay=10,
            matik_api_read_timeout=5.0,
            matik_api_write_timeout=15.0,
            lookback_hours_github=12,
            lookback_hours_jira=48,
            correlation_system_prompt="Custom prompt",
            min_llm_score=0.5,
            speculative_patterns=[r"\bmaybe\b"],
            speculative_max_score=0.7,
        )
        assert (
            config.sqs_queue_url == "https://sqs.us-west-2.amazonaws.com/123/my-queue"
        )
        assert (
            config.scribe_queue_url
            == "https://sqs.us-west-2.amazonaws.com/123/scribe-high"
        )
        assert config.sqs_queue_region == "us-west-2"
        assert config.max_concurrent_correlations == 10
        assert config.sqs_max_messages == 5
        assert config.sqs_wait_time_seconds == 10
        assert config.sqs_visibility_timeout == 600
        assert config.sqs_poll_error_delay == 10
        assert config.matik_api_read_timeout == 5.0
        assert config.matik_api_write_timeout == 15.0
        assert config.lookback_hours_github == 12
        assert config.lookback_hours_jira == 48
        assert config.correlation_system_prompt == "Custom prompt"
        assert config.min_llm_score == 0.5
        assert config.speculative_patterns == [r"\bmaybe\b"]
        assert config.speculative_max_score == 0.7

    def test_model_validate_from_dict(self) -> None:
        """Test creating model from dict."""
        data = {
            "correlation_system_prompt": "test prompt",
            "sqs_queue_url": "https://sqs.example.com/queue",
            "scribe_queue_url": _SCRIBE_URL,
            "speculative_patterns": [],
        }
        config = EnigmatologistConfig.model_validate(data)
        assert config.correlation_system_prompt == "test prompt"
        assert config.sqs_queue_url == "https://sqs.example.com/queue"
        assert config.scribe_queue_url == _SCRIBE_URL

    def test_model_dump(self) -> None:
        """Test serializing model to dict."""
        config = EnigmatologistConfig(
            scribe_queue_url=_SCRIBE_URL,
            correlation_system_prompt="test prompt",
            speculative_patterns=[],
        )
        data = config.model_dump()
        assert data["correlation_system_prompt"] == "test prompt"
        assert data["sqs_queue_url"] == ""
        assert data["scribe_queue_url"] == _SCRIBE_URL
        assert data["min_llm_score"] == 0.3


class TestValidateRegexPatterns:
    """Tests for the speculative_patterns regex validator."""

    def test_valid_patterns_accepted(self) -> None:
        """Valid regex patterns are accepted without error."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            speculative_patterns=[r"\bmay(be)?\b", r"\bcould\b", r"\bpotentially\b"],
        )
        assert len(config.speculative_patterns) == 3

    def test_empty_list_accepted(self) -> None:
        """Empty list is accepted."""
        config = EnigmatologistConfig(scribe_queue_url="", speculative_patterns=[])
        assert config.speculative_patterns == []

    def test_invalid_regex_raises(self) -> None:
        """Invalid regex raises a ValueError at construction time."""
        with pytest.raises(ValueError):
            EnigmatologistConfig(
                scribe_queue_url="", speculative_patterns=[r"[invalid"]
            )

    def test_plain_strings_are_valid_regex(self) -> None:
        """Plain strings with no special chars are valid regex patterns."""
        config = EnigmatologistConfig(
            scribe_queue_url="", speculative_patterns=["maybe", "probably"]
        )
        assert config.speculative_patterns == ["maybe", "probably"]


class TestNormalizeEmptyStrings:
    """Tests for the sqs_queue_region validator."""

    def test_empty_string_becomes_none(self) -> None:
        """Test that empty string region is normalized to None."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region="",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.sqs_queue_region is None

    def test_whitespace_only_becomes_none(self) -> None:
        """Test that whitespace-only region is normalized to None."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region="   ",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.sqs_queue_region is None

    def test_none_stays_none(self) -> None:
        """Test that None region stays None."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region=None,
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.sqs_queue_region is None

    def test_valid_string_kept(self) -> None:
        """Test that a valid region string is preserved."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region="eu-west-1",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.sqs_queue_region == "eu-west-1"


class TestRegionProperty:
    """Tests for the region computed property."""

    def test_region_fallback_when_none(self) -> None:
        """Test that region falls back to us-east-1 when sqs_queue_region is None."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.region == "us-east-1"

    def test_region_fallback_when_empty_string(self) -> None:
        """Test that region falls back when sqs_queue_region is empty string (normalized to None)."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region="",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.region == "us-east-1"

    def test_region_uses_configured_value(self) -> None:
        """Test that region uses the configured sqs_queue_region."""
        config = EnigmatologistConfig(
            scribe_queue_url="",
            sqs_queue_region="ap-southeast-1",
            correlation_system_prompt="test",
            speculative_patterns=[],
        )
        assert config.region == "ap-southeast-1"
