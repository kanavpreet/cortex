"""Unit tests for the async SQSClient wrapper."""

from unittest.mock import MagicMock, patch

from common.queues.sqs_client import SQSClient


def _make_client() -> tuple[SQSClient, MagicMock]:
    """Create an SQSClient with a mocked underlying boto3 SQS client.

    Returns:
        Tuple of (SQSClient instance, mock boto3 sqs client).
    """
    with patch("common.queues.sqs_client.boto3") as mock_boto3:
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_boto3_client = MagicMock()
        mock_session.client.return_value = mock_boto3_client
        client = SQSClient(region="us-east-1")
        return client, mock_boto3_client


class TestSQSClientInit:
    """Tests for SQSClient initialization."""

    def test_init_uses_specified_region(self) -> None:
        """SQSClient initializes boto3.Session with the given region."""
        with patch("common.queues.sqs_client.boto3") as mock_boto3:
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session
            mock_session.client.return_value = MagicMock()

            SQSClient(region="eu-west-1")

            mock_boto3.Session.assert_called_once_with(region_name="eu-west-1")
            mock_session.client.assert_called_once_with("sqs")

    def test_init_default_region(self) -> None:
        """SQSClient defaults to us-east-1 when no region is specified."""
        with patch("common.queues.sqs_client.boto3") as mock_boto3:
            mock_session = MagicMock()
            mock_boto3.Session.return_value = mock_session
            mock_session.client.return_value = MagicMock()

            SQSClient()

            mock_boto3.Session.assert_called_once_with(region_name="us-east-1")


class TestReceiveMessages:
    """Tests for SQSClient.receive_messages."""

    async def test_returns_messages_from_response(self) -> None:
        """receive_messages returns the Messages list from the boto3 response."""
        client, boto3_client = _make_client()
        expected = [{"Body": '{"x": 1}', "ReceiptHandle": "rh-1"}]
        boto3_client.receive_message.return_value = {"Messages": expected}

        result = await client.receive_messages("https://sqs/queue", max_messages=5)

        assert result == expected
        boto3_client.receive_message.assert_called_once_with(
            QueueUrl="https://sqs/queue",
            MaxNumberOfMessages=5,
            WaitTimeSeconds=20,
            VisibilityTimeout=300,
            AttributeNames=["ApproximateReceiveCount"],
        )

    async def test_returns_empty_list_when_no_messages(self) -> None:
        """receive_messages returns empty list when SQS response has no Messages key."""
        client, boto3_client = _make_client()
        boto3_client.receive_message.return_value = {}

        result = await client.receive_messages("https://sqs/queue")

        assert result == []

    async def test_passes_custom_parameters(self) -> None:
        """receive_messages forwards custom max_messages and wait_time_seconds."""
        client, boto3_client = _make_client()
        boto3_client.receive_message.return_value = {"Messages": []}

        await client.receive_messages(
            "https://sqs/queue",
            max_messages=3,
            wait_time_seconds=10,
            visibility_timeout=60,
        )

        call_kwargs = boto3_client.receive_message.call_args[1]
        assert call_kwargs["MaxNumberOfMessages"] == 3
        assert call_kwargs["WaitTimeSeconds"] == 10
        assert call_kwargs["VisibilityTimeout"] == 60

    async def test_requests_approximate_receive_count_attribute(self) -> None:
        """receive_messages requests ApproximateReceiveCount so backoff tiers work correctly.

        Without AttributeNames=['ApproximateReceiveCount'], SQS does not include
        the Attributes dict in responses, making receive_count always fall back to '1'
        and preventing backoff from escalating beyond the first tier.
        """
        client, boto3_client = _make_client()
        boto3_client.receive_message.return_value = {"Messages": []}

        await client.receive_messages("https://sqs/queue")

        call_kwargs = boto3_client.receive_message.call_args[1]
        assert "AttributeNames" in call_kwargs
        assert "ApproximateReceiveCount" in call_kwargs["AttributeNames"]


class TestDeleteMessage:
    """Tests for SQSClient.delete_message."""

    async def test_calls_boto3_delete_with_correct_args(self) -> None:
        """delete_message calls the underlying boto3 delete_message correctly."""
        client, boto3_client = _make_client()
        boto3_client.delete_message.return_value = {}

        await client.delete_message("https://sqs/queue", "receipt-handle-xyz")

        boto3_client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs/queue",
            ReceiptHandle="receipt-handle-xyz",
        )


class TestSendMessage:
    """Tests for SQSClient.send_message."""

    async def test_sends_message_and_returns_response(self) -> None:
        """send_message calls boto3 and returns the response dict."""
        client, boto3_client = _make_client()
        boto3_client.send_message.return_value = {"MessageId": "msg-123"}

        result = await client.send_message("https://sqs/out-queue", '{"key": "val"}')

        assert result == {"MessageId": "msg-123"}
        boto3_client.send_message.assert_called_once_with(
            QueueUrl="https://sqs/out-queue",
            MessageBody='{"key": "val"}',
        )


class TestChangeMessageVisibility:
    """Tests for SQSClient.change_message_visibility."""

    async def test_calls_boto3_with_correct_args(self) -> None:
        """change_message_visibility calls underlying boto3 method correctly."""
        client, boto3_client = _make_client()
        boto3_client.change_message_visibility.return_value = {}

        await client.change_message_visibility("https://sqs/queue", "rh-abc", 60)

        boto3_client.change_message_visibility.assert_called_once_with(
            QueueUrl="https://sqs/queue",
            ReceiptHandle="rh-abc",
            VisibilityTimeout=60,
        )


class TestGetQueueAttributes:
    """Tests for SQSClient.get_queue_attributes."""

    async def test_returns_attributes_dict(self) -> None:
        """get_queue_attributes returns the Attributes dict from the boto3 response."""
        client, boto3_client = _make_client()
        boto3_client.get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": "42"}
        }

        result = await client.get_queue_attributes(
            "https://sqs/queue", ["ApproximateNumberOfMessages"]
        )

        assert result == {"ApproximateNumberOfMessages": "42"}
        boto3_client.get_queue_attributes.assert_called_once_with(
            QueueUrl="https://sqs/queue",
            AttributeNames=["ApproximateNumberOfMessages"],
        )

    async def test_returns_empty_dict_when_no_attributes(self) -> None:
        """get_queue_attributes returns empty dict when response has no Attributes key."""
        client, boto3_client = _make_client()
        boto3_client.get_queue_attributes.return_value = {}

        result = await client.get_queue_attributes("https://sqs/queue", [])

        assert result == {}
