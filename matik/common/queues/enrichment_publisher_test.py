"""Unit tests for EnrichmentPublisher and EnrichmentPublisherSync."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.models.enricher_messages import EnrichmentRequest
from common.queues.enrichment_publisher import (
    EnrichmentPublisher,
    EnrichmentPublisherSync,
)


@pytest.fixture
def sample_enrichment_request() -> EnrichmentRequest:
    """Create a sample enrichment request for testing."""
    return EnrichmentRequest(
        source_type="incidentio",
        producer="historian",
        task_id="test-task-123",
        entity_id={"incident_id": "INC-001"},
        content={"summary": "Test incident summary"},
    )


class TestEnrichmentPublisher:
    """Test suite for async EnrichmentPublisher."""

    @pytest.fixture
    def mock_sqs_client(self) -> AsyncMock:
        """Create a mock SQS client."""
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"MessageId": "msg-123"})
        return client

    @pytest.fixture
    def publisher(self, mock_sqs_client: AsyncMock) -> EnrichmentPublisher:
        """Create an EnrichmentPublisher with mock client."""
        return EnrichmentPublisher(
            sqs_client=mock_sqs_client,
            queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
        )

    async def test_publish_success(
        self,
        publisher: EnrichmentPublisher,
        mock_sqs_client: AsyncMock,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test successful message publication."""
        result = await publisher.publish(sample_enrichment_request)

        assert result == "msg-123"
        mock_sqs_client.send_message.assert_called_once()
        call_args = mock_sqs_client.send_message.call_args
        assert (
            call_args[0][0]
            == "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"
        )

    async def test_publish_includes_all_fields(
        self,
        publisher: EnrichmentPublisher,
        mock_sqs_client: AsyncMock,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test that published message includes all request fields."""
        import json

        await publisher.publish(sample_enrichment_request)

        call_args = mock_sqs_client.send_message.call_args
        message_body = json.loads(call_args[0][1])
        assert message_body["source_type"] == "incidentio"
        assert message_body["producer"] == "historian"
        assert message_body["task_id"] == "test-task-123"
        assert message_body["entity_id"] == {"incident_id": "INC-001"}
        assert message_body["content"] == {"summary": "Test incident summary"}

    async def test_publish_failure_returns_none(
        self,
        publisher: EnrichmentPublisher,
        mock_sqs_client: AsyncMock,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test that publish returns None on exception."""
        mock_sqs_client.send_message.side_effect = Exception("SQS error")

        result = await publisher.publish(sample_enrichment_request)

        assert result is None

    async def test_publish_missing_message_id(
        self,
        publisher: EnrichmentPublisher,
        mock_sqs_client: AsyncMock,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test handling response without MessageId."""
        mock_sqs_client.send_message.return_value = {}

        result = await publisher.publish(sample_enrichment_request)

        assert result is None

    async def test_publish_serializes_entered_at_datetime(
        self,
        publisher: EnrichmentPublisher,
        mock_sqs_client: AsyncMock,
    ) -> None:
        """entered_at is a real datetime on the model; publish must not raise
        TypeError from json.dumps on a non-JSON-serializable object."""
        request = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="test-task-123",
            entity_id={"incident_id": "INC-001"},
            content={"summary": "Test incident summary"},
            entered_at=datetime(2026, 7, 29, 4, 18, 47),
        )

        result = await publisher.publish(request)

        assert result == "msg-123"
        call_args = mock_sqs_client.send_message.call_args
        assert call_args[0][1] == request.model_dump_json()


class TestEnrichmentPublisherSync:
    """Test suite for synchronous EnrichmentPublisherSync."""

    @pytest.fixture
    def mock_boto3_client(self) -> MagicMock:
        """Create a mock boto3 SQS client."""
        client = MagicMock()
        client.send_message = MagicMock(return_value={"MessageId": "sync-msg-456"})
        return client

    @pytest.fixture
    def publisher_sync(self, mock_boto3_client: MagicMock) -> EnrichmentPublisherSync:
        """Create an EnrichmentPublisherSync with mock client."""
        with patch("boto3.client", return_value=mock_boto3_client):
            return EnrichmentPublisherSync(
                queue_url="https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
                region="us-east-1",
            )

    def test_publish_sync_success(
        self,
        publisher_sync: EnrichmentPublisherSync,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test successful synchronous message publication."""
        result = publisher_sync.publish(sample_enrichment_request)

        assert result == "sync-msg-456"

    def test_publish_sync_includes_queue_url(
        self,
        publisher_sync: EnrichmentPublisherSync,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test that publish uses correct queue URL."""
        publisher_sync.publish(sample_enrichment_request)

        call_kwargs = publisher_sync._client.send_message.call_args[1]
        assert (
            call_kwargs["QueueUrl"]
            == "https://sqs.us-east-1.amazonaws.com/123456789/test-queue"
        )

    def test_publish_sync_includes_message_body(
        self,
        publisher_sync: EnrichmentPublisherSync,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test that publish includes serialized message body."""
        import json

        publisher_sync.publish(sample_enrichment_request)

        call_kwargs = publisher_sync._client.send_message.call_args[1]
        message_body = json.loads(call_kwargs["MessageBody"])
        assert message_body["source_type"] == "incidentio"
        assert message_body["task_id"] == "test-task-123"

    def test_publish_sync_failure_returns_none(
        self,
        publisher_sync: EnrichmentPublisherSync,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test that sync publish returns None on exception."""
        publisher_sync._client.send_message.side_effect = Exception("SQS error")

        result = publisher_sync.publish(sample_enrichment_request)

        assert result is None

    def test_publish_sync_missing_message_id(
        self,
        publisher_sync: EnrichmentPublisherSync,
        sample_enrichment_request: EnrichmentRequest,
    ) -> None:
        """Test handling response without MessageId."""
        publisher_sync._client.send_message.return_value = {}

        result = publisher_sync.publish(sample_enrichment_request)

        assert result is None

    def test_publish_sync_serializes_entered_at_datetime(
        self,
        publisher_sync: EnrichmentPublisherSync,
    ) -> None:
        """entered_at is a real datetime on the model; publish must not raise
        TypeError from json.dumps on a non-JSON-serializable object."""
        request = EnrichmentRequest(
            source_type="incidentio",
            producer="historian",
            task_id="test-task-123",
            entity_id={"incident_id": "INC-001"},
            content={"summary": "Test incident summary"},
            entered_at=datetime(2026, 7, 29, 4, 18, 47),
        )

        result = publisher_sync.publish(request)

        assert result == "sync-msg-456"
        call_kwargs = publisher_sync._client.send_message.call_args[1]
        assert call_kwargs["MessageBody"] == request.model_dump_json()

    def test_init_creates_boto3_client(self) -> None:
        """Test that init creates boto3 SQS client with correct region."""
        with patch("boto3.client") as mock_client:
            mock_client.return_value = MagicMock()
            EnrichmentPublisherSync(
                queue_url="https://sqs.us-west-2.amazonaws.com/123/queue",
                region="us-west-2",
            )

            mock_client.assert_called_once_with("sqs", region_name="us-west-2")

    def test_init_default_region(self) -> None:
        """Test that init uses default region us-east-1."""
        with patch("boto3.client") as mock_client:
            mock_client.return_value = MagicMock()
            EnrichmentPublisherSync(
                queue_url="https://sqs.us-east-1.amazonaws.com/123/queue",
            )

            mock_client.assert_called_once_with("sqs", region_name="us-east-1")
