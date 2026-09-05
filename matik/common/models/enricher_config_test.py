"""Unit tests for EnricherConfig and SourceMappingEntry models."""

from typing import Any

import pytest
from pydantic import ValidationError

from common.models.enricher_config import EnricherConfig, SourceMappingEntry


class TestSourceMappingEntry:
    """Tests for the SourceMappingEntry model."""

    def test_valid_construction(self) -> None:
        """Valid SourceMappingEntry parses all required fields."""
        entry = SourceMappingEntry(
            input_keys=["summary"],
            output_field="root_cause_summary",
            prompt="You are an incident analyst.",
            hash_field="root_cause_summary_hash",
        )
        assert entry.input_keys == ["summary"]
        assert entry.output_field == "root_cause_summary"
        assert entry.prompt == "You are an incident analyst."
        assert entry.hash_field == "root_cause_summary_hash"

    def test_multi_key_input_keys(self) -> None:
        """SourceMappingEntry with multiple input_keys is valid."""
        entry = SourceMappingEntry(
            input_keys=["summary", "resolution_statement"],
            output_field="root_cause_summary",
            prompt="Summarize the root cause.",
            hash_field="root_cause_summary_hash",
        )
        assert len(entry.input_keys) == 2
        assert "resolution_statement" in entry.input_keys

    def test_hash_field_required(self) -> None:
        """Missing hash_field raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceMappingEntry(
                input_keys=["summary"],
                output_field="root_cause_summary",
                prompt="p",
            )

    def test_hash_field_correct_shape(self) -> None:
        """hash_field is a single DB column name string."""
        entry = SourceMappingEntry(
            input_keys=["summary", "resolution_statement"],
            output_field="root_cause_summary",
            prompt="p",
            hash_field="root_cause_summary_hash",
        )
        assert entry.hash_field == "root_cause_summary_hash"

    def test_missing_input_keys_raises(self) -> None:
        """Missing input_keys raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceMappingEntry(output_field="out", prompt="p", hash_field="h")

    def test_missing_output_field_raises(self) -> None:
        """Missing output_field raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceMappingEntry(input_keys=["k"], prompt="p", hash_field="h")

    def test_missing_prompt_raises(self) -> None:
        """Missing prompt raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceMappingEntry(input_keys=["k"], output_field="o", hash_field="h")


class TestEnricherConfigDefaults:
    """Tests for EnricherConfig default values."""

    def _minimal_config(self) -> dict[str, Any]:
        """Return minimal required fields for EnricherConfig."""
        return {
            "enricher_queue_url": "https://sqs.us-east-1.amazonaws.com/123/enricher",
            "enricher_dlq_url": "https://sqs.us-east-1.amazonaws.com/123/enricher-dlq",
            "scribe_llm_queue_url": "https://sqs.us-east-1.amazonaws.com/123/scribe",
            "general_prompt": "General: {source_instructions}",
            "source_mappings": {
                "incidentio": [
                    {
                        "input_keys": ["summary"],
                        "output_field": "root_cause_summary",
                        "prompt": "Root cause prompt.",
                        "hash_field": "root_cause_summary_hash",
                    }
                ]
            },
        }

    def test_sqs_max_messages_default(self) -> None:
        """sqs_max_messages defaults to 10."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.sqs_max_messages == 10

    def test_sqs_wait_time_seconds_default(self) -> None:
        """sqs_wait_time_seconds defaults to 20."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.sqs_wait_time_seconds == 20

    def test_sqs_poll_error_delay_default(self) -> None:
        """sqs_poll_error_delay defaults to 5."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.sqs_poll_error_delay == 5

    def test_max_concurrent_llm_calls_default(self) -> None:
        """max_concurrent_llm_calls defaults to 20."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.max_concurrent_llm_calls == 20

    def test_visibility_timeout_seconds_default(self) -> None:
        """visibility_timeout_seconds defaults to 300."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.visibility_timeout_seconds == 300

    def test_sqs_queue_region_defaults_to_none(self) -> None:
        """sqs_queue_region defaults to None."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.sqs_queue_region is None

    def test_region_property_falls_back_to_us_east_1(self) -> None:
        """region property returns us-east-1 when sqs_queue_region is None."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.region == "us-east-1"

    def test_region_property_uses_configured_value(self) -> None:
        """region property returns configured value when set."""
        data = self._minimal_config()
        data["sqs_queue_region"] = "us-west-2"
        cfg = EnricherConfig(**data)
        assert cfg.region == "us-west-2"

    def test_hash_cache_ttl_seconds_default(self) -> None:
        """hash_cache_ttl_seconds defaults to 2592000 (30 days)."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.hash_cache_ttl_seconds == 2592000

    def test_hash_cache_max_size_default(self) -> None:
        """hash_cache_max_size defaults to 50000."""
        cfg = EnricherConfig(**self._minimal_config())
        assert cfg.hash_cache_max_size == 50000

    def test_hash_cache_ttl_seconds_configurable(self) -> None:
        """hash_cache_ttl_seconds can be overridden."""
        data = self._minimal_config()
        data["hash_cache_ttl_seconds"] = 600
        cfg = EnricherConfig(**data)
        assert cfg.hash_cache_ttl_seconds == 600

    def test_hash_cache_max_size_configurable(self) -> None:
        """hash_cache_max_size can be overridden."""
        data = self._minimal_config()
        data["hash_cache_max_size"] = 5000
        cfg = EnricherConfig(**data)
        assert cfg.hash_cache_max_size == 5000


class TestEnricherConfigBuildPrompt:
    """Tests for the build_prompt method."""

    def _make_config(self, general_prompt: str) -> EnricherConfig:
        return EnricherConfig(
            enricher_queue_url="https://sqs.example.com/enricher",
            enricher_dlq_url="https://sqs.example.com/dlq",
            scribe_llm_queue_url="https://sqs.example.com/scribe",
            general_prompt=general_prompt,
            source_mappings={},
        )

    def test_build_prompt_substitutes_source_instructions(self) -> None:
        """build_prompt correctly substitutes source_instructions placeholder."""
        cfg = self._make_config("Base: {source_instructions}")
        result = cfg.build_prompt("Specific instructions here.")
        assert result == "Base: Specific instructions here."

    def test_build_prompt_with_multiline_instructions(self) -> None:
        """build_prompt works with multi-line source instructions."""
        cfg = self._make_config("Header\n{source_instructions}\nFooter")
        result = cfg.build_prompt("Line 1\nLine 2")
        assert "Line 1\nLine 2" in result
        assert "Header" in result
        assert "Footer" in result

    def test_build_prompt_no_placeholder_returns_unchanged(self) -> None:
        """build_prompt with no placeholder returns the template unchanged."""
        cfg = self._make_config("No placeholder here")
        result = cfg.build_prompt("something")
        assert result == "No placeholder here"

    def test_build_prompt_wrong_placeholder_raises_key_error(self) -> None:
        """build_prompt raises KeyError if general_prompt has an unknown placeholder."""
        cfg = self._make_config("Header {wrong_key} Footer")
        with pytest.raises(KeyError):
            cfg.build_prompt("something")


class TestEnricherConfigSourceMappings:
    """Tests for source_mappings validation and structure."""

    def test_multiple_source_types_accepted(self) -> None:
        """source_mappings with multiple source types is valid."""
        cfg = EnricherConfig(
            enricher_queue_url="https://sqs.example.com/enricher",
            enricher_dlq_url="https://sqs.example.com/dlq",
            scribe_llm_queue_url="https://sqs.example.com/scribe",
            general_prompt="{source_instructions}",
            source_mappings={
                "incidentio": [
                    SourceMappingEntry(
                        input_keys=["summary"],
                        output_field="root_cause_summary",
                        prompt="prompt1",
                        hash_field="root_cause_summary_hash",
                    )
                ],
                "ghe_pr": [
                    SourceMappingEntry(
                        input_keys=["original_description"],
                        output_field="pull_request_summary",
                        prompt="prompt2",
                        hash_field="description_hash",
                    )
                ],
            },
        )
        assert "incidentio" in cfg.source_mappings
        assert "ghe_pr" in cfg.source_mappings
        assert cfg.source_mappings["incidentio"][0].output_field == "root_cause_summary"

    def test_missing_required_fields_raises(self) -> None:
        """Missing enricher_queue_url raises ValidationError."""
        with pytest.raises(ValidationError):
            EnricherConfig(
                enricher_dlq_url="https://sqs.example.com/dlq",
                scribe_llm_queue_url="https://sqs.example.com/scribe",
                general_prompt="{source_instructions}",
                source_mappings={},
            )
