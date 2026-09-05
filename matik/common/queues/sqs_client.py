"""Lightweight async wrapper around boto3 SQS client."""

import asyncio
from typing import Any

import boto3

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class SQSClient:
    """Async wrapper around the boto3 SQS client.

    All blocking boto3 calls are executed in a thread pool via
    ``asyncio.to_thread`` so they don't block the event loop.

    Usage::

        client = SQSClient(region="us-east-1")

        messages = await client.receive_messages(queue_url, max_messages=10)
        for msg in messages:
            await client.delete_message(queue_url, msg["ReceiptHandle"])
    """

    def __init__(self, region: str = "us-east-1") -> None:
        """Initialize the async SQS client.

        Args:
            region: AWS region for the SQS client. Defaults to us-east-1.
        """
        session = boto3.Session(region_name=region)
        self._client = session.client("sqs")
        logger.info("SQSClient initialized", region=region)

    async def receive_messages(
        self,
        queue_url: str,
        max_messages: int = 10,
        wait_time_seconds: int = 20,
        visibility_timeout: int = 300,
    ) -> list[dict[str, Any]]:
        """Long-poll SQS and return a list of messages.

        Args:
            queue_url: The SQS queue URL to receive messages from.
            max_messages: Maximum number of messages to retrieve (1-10).
            wait_time_seconds: Long-poll wait time in seconds (0-20).
            visibility_timeout: Message visibility timeout in seconds while processing.

        Returns:
            A list of SQS message dicts, each containing at least
            ``Body`` and ``ReceiptHandle`` keys. Empty list if no messages.
        """
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_seconds,
            VisibilityTimeout=visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        return list(response.get("Messages", []))

    async def delete_message(self, queue_url: str, receipt_handle: str) -> None:
        """Delete a successfully processed message from the queue.

        Args:
            queue_url: The SQS queue URL the message was received from.
            receipt_handle: The receipt handle of the message to delete.
        """
        await asyncio.to_thread(
            self._client.delete_message,
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
        )

    async def send_message(self, queue_url: str, message_body: str) -> dict[str, Any]:
        """Send a message to an SQS queue.

        Args:
            queue_url: The SQS queue URL to send the message to.
            message_body: The message body string (typically JSON-encoded).

        Returns:
            The boto3 send_message response dict containing MessageId, etc.
        """
        return await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=queue_url,
            MessageBody=message_body,
        )

    async def change_message_visibility(
        self,
        queue_url: str,
        receipt_handle: str,
        visibility_timeout: int,
    ) -> None:
        """Update the visibility timeout of an in-flight message.

        Used to implement retry backoff: extend the timeout so the message
        won't be redelivered until the backoff period has elapsed.

        Args:
            queue_url: The SQS queue URL the message was received from.
            receipt_handle: The receipt handle of the message to update.
            visibility_timeout: New visibility timeout in seconds (0-43200).
        """
        await asyncio.to_thread(
            self._client.change_message_visibility,
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=visibility_timeout,
        )

    async def get_queue_attributes(
        self, queue_url: str, attribute_names: list[str]
    ) -> dict[str, str]:
        """Retrieve attributes for an SQS queue.

        Args:
            queue_url: The SQS queue URL to query.
            attribute_names: List of attribute names to retrieve
                (e.g., ["ApproximateNumberOfMessages"]).

        Returns:
            A dict mapping attribute name to attribute value string.
        """
        response = await asyncio.to_thread(
            self._client.get_queue_attributes,
            QueueUrl=queue_url,
            AttributeNames=attribute_names,
        )
        return dict(response.get("Attributes", {}))
