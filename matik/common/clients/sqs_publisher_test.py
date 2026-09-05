"""Tests for SQSPublisher."""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from common.clients.sqs_publisher import SQSPublisher


class _SampleMessage(BaseModel):
    source_type: str = "test"
    message_type: str = "base"
    value: int = 42


class TestSQSPublisher:
    """Test SQSPublisher.send."""

    @pytest.mark.asyncio
    async def test_send_calls_sqs_with_serialized_message(self) -> None:
        """Test send() calls sqs_client.send_message with the correct args."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        message = _SampleMessage()

        await publisher.send(message)

        mock_sqs_client.send_message.assert_called_once_with(
            QueueUrl="https://sqs.us-east-1.amazonaws.com/123/test-queue",
            MessageBody=message.model_dump_json(),
        )

    @pytest.mark.asyncio
    async def test_send_serializes_message_as_valid_json(self) -> None:
        """Test that the MessageBody is valid JSON containing the expected fields."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        message = _SampleMessage(value=99)

        await publisher.send(message)

        body = mock_sqs_client.send_message.call_args.kwargs["MessageBody"]
        parsed = json.loads(body)
        assert parsed["source_type"] == "test"
        assert parsed["message_type"] == "base"
        assert parsed["value"] == 99

    @pytest.mark.asyncio
    async def test_send_propagates_sqs_errors(self) -> None:
        """Test that boto3 errors propagate to the caller."""
        mock_sqs_client = MagicMock()
        mock_sqs_client.send_message.side_effect = RuntimeError("SQS unavailable")
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )

        with pytest.raises(RuntimeError, match="SQS unavailable"):
            await publisher.send(_SampleMessage())

    @pytest.mark.asyncio
    async def test_send_uses_asyncio_to_thread(self) -> None:
        """Test that send() wraps the boto3 call in asyncio.to_thread."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )

        with patch("common.clients.sqs_publisher.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = None
            await publisher.send(_SampleMessage())

        mock_to_thread.assert_called_once()
        args = mock_to_thread.call_args[0]
        assert args[0] == mock_sqs_client.send_message

    def test_send_sync_calls_sqs_with_serialized_message(self) -> None:
        """Test send_sync() calls sqs_client.send_message synchronously with correct args."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        message = _SampleMessage(value=7)

        publisher.send_sync(message)

        mock_sqs_client.send_message.assert_called_once_with(
            QueueUrl="https://sqs.us-east-1.amazonaws.com/123/test-queue",
            MessageBody=message.model_dump_json(),
        )

    def test_send_sync_propagates_sqs_errors(self) -> None:
        """Test that boto3 errors from send_sync propagate to the caller."""
        mock_sqs_client = MagicMock()
        mock_sqs_client.send_message.side_effect = RuntimeError("SQS unavailable")
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )

        with pytest.raises(RuntimeError, match="SQS unavailable"):
            publisher.send_sync(_SampleMessage())


class TestSQSPublisherQueueName:
    """Test queue name extraction from URL."""

    def test_queue_name_extracted_from_url(self) -> None:
        """Queue name is parsed from the last path segment of the URL."""
        publisher = SQSPublisher(
            MagicMock(), "https://sqs.us-east-1.amazonaws.com/123/my-queue-name"
        )
        assert publisher._queue_name == "my-queue-name"

    def test_queue_name_with_trailing_slash(self) -> None:
        """Trailing slash is stripped before extracting queue name."""
        publisher = SQSPublisher(
            MagicMock(), "https://sqs.us-east-1.amazonaws.com/123/my-queue/"
        )
        assert publisher._queue_name == "my-queue"


class TestSQSPublisherMetricsIntegration:
    """Test SQSPublisher metrics instrumentation."""

    @pytest.mark.asyncio
    async def test_send_records_success_metric(self) -> None:
        """send() calls metrics.start_publish and records success."""
        mock_sqs_client = MagicMock()
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_publish.return_value = mock_record

        publisher = SQSPublisher(
            mock_sqs_client,
            "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            mock_metrics,
        )
        await publisher.send(_SampleMessage())

        mock_metrics.start_publish.assert_called_once_with(
            "test-queue", "_SampleMessage"
        )
        mock_record.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_send_records_failure_metric(self) -> None:
        """send() records failure metric and re-raises on error."""
        mock_sqs_client = MagicMock()
        mock_sqs_client.send_message.side_effect = RuntimeError("SQS unavailable")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_publish.return_value = mock_record

        publisher = SQSPublisher(
            mock_sqs_client,
            "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            mock_metrics,
        )

        with pytest.raises(RuntimeError):
            await publisher.send(_SampleMessage())

        mock_record.assert_called_once()
        _, kwargs = mock_record.call_args
        assert kwargs.get("success") is False or mock_record.call_args[0][0] is False

    def test_send_sync_records_success_metric(self) -> None:
        """send_sync() calls metrics.start_publish and records success."""
        mock_sqs_client = MagicMock()
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_publish.return_value = mock_record

        publisher = SQSPublisher(
            mock_sqs_client,
            "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            mock_metrics,
        )
        publisher.send_sync(_SampleMessage())

        mock_metrics.start_publish.assert_called_once_with(
            "test-queue", "_SampleMessage"
        )
        mock_record.assert_called_once_with(True)

    def test_send_sync_records_failure_metric(self) -> None:
        """send_sync() records failure metric and re-raises on error."""
        mock_sqs_client = MagicMock()
        mock_sqs_client.send_message.side_effect = RuntimeError("SQS unavailable")
        mock_metrics = MagicMock()
        mock_record = MagicMock()
        mock_metrics.start_publish.return_value = mock_record

        publisher = SQSPublisher(
            mock_sqs_client,
            "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            mock_metrics,
        )

        with pytest.raises(RuntimeError):
            publisher.send_sync(_SampleMessage())

        mock_record.assert_called_once()
        _, kwargs = mock_record.call_args
        assert kwargs.get("success") is False or mock_record.call_args[0][0] is False

    @pytest.mark.asyncio
    async def test_send_without_metrics_does_not_fail(self) -> None:
        """send() works normally when no metrics instance is provided."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        await publisher.send(_SampleMessage())
        mock_sqs_client.send_message.assert_called_once()

    def test_send_sync_without_metrics_does_not_fail(self) -> None:
        """send_sync() works normally when no metrics instance is provided."""
        mock_sqs_client = MagicMock()
        publisher = SQSPublisher(
            mock_sqs_client, "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        publisher.send_sync(_SampleMessage())
        mock_sqs_client.send_message.assert_called_once()
