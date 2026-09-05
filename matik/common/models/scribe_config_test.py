"""Tests for Scribe service configuration models."""

import pytest
from pydantic import ValidationError

from common.models.scribe_config import ScribeConfig, ScribeQueueConfig


def _queue(
    queue_url: str = "https://sqs.example.com/q",
    dlq_url: str = "https://sqs.example.com/q-dlq",
) -> ScribeQueueConfig:
    return ScribeQueueConfig(
        queue_url=queue_url,
        dlq_url=dlq_url,
    )


class TestScribeQueueConfig:
    def test_required_fields(self) -> None:
        q = _queue()
        assert q.queue_url == "https://sqs.example.com/q"
        assert q.dlq_url == "https://sqs.example.com/q-dlq"

    def test_missing_queue_url_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeQueueConfig(dlq_url="https://sqs.example.com/dlq")

    def test_missing_dlq_url_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeQueueConfig(queue_url="https://sqs.example.com/q")


class TestScribeConfigDefaults:
    def test_sqs_defaults(self) -> None:
        cfg = ScribeConfig(queue=_queue())
        assert cfg.sqs_max_messages == 10
        assert cfg.sqs_wait_time_seconds == 20
        assert cfg.sqs_visibility_timeout == 300
        assert cfg.sqs_poll_error_delay == 5
        assert cfg.max_concurrent_writes == 10

    def test_sqs_queue_region_defaults_to_none(self) -> None:
        cfg = ScribeConfig(queue=_queue())
        assert cfg.sqs_queue_region is None

    def test_missing_queue_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeConfig()


class TestNormalizeEmptyStrings:
    def test_none_region_stays_none(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region=None)
        assert cfg.sqs_queue_region is None

    def test_valid_region_preserved(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region="us-west-2")
        assert cfg.sqs_queue_region == "us-west-2"

    def test_empty_string_region_becomes_none(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region="")
        assert cfg.sqs_queue_region is None

    def test_whitespace_only_region_becomes_none(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region="   ")
        assert cfg.sqs_queue_region is None


class TestBatchFields:
    def test_defaults(self) -> None:
        cfg = ScribeConfig(queue=_queue())
        assert cfg.batch_max_messages == 25
        assert cfg.batch_flush_interval_ms == 500

    def test_custom_values(self) -> None:
        cfg = ScribeConfig(
            queue=_queue(), batch_max_messages=100, batch_flush_interval_ms=5000
        )
        assert cfg.batch_max_messages == 100
        assert cfg.batch_flush_interval_ms == 5000

    def test_zero_max_messages_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeConfig(queue=_queue(), batch_max_messages=0)

    def test_negative_max_messages_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeConfig(queue=_queue(), batch_max_messages=-1)

    def test_zero_flush_interval_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeConfig(queue=_queue(), batch_flush_interval_ms=0)

    def test_negative_flush_interval_raises(self) -> None:
        with pytest.raises(ValidationError):
            ScribeConfig(queue=_queue(), batch_flush_interval_ms=-100)


class TestRegionProperty:
    def test_region_returns_configured_value(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region="eu-west-1")
        assert cfg.region == "eu-west-1"

    def test_region_defaults_to_us_east_1_when_none(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region=None)
        assert cfg.region == "us-east-1"

    def test_region_defaults_to_us_east_1_when_empty(self) -> None:
        cfg = ScribeConfig(queue=_queue(), sqs_queue_region="")
        assert cfg.region == "us-east-1"
