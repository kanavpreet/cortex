"""Reusable SQS message publisher for historian services."""

import asyncio
from typing import Any

from pydantic import BaseModel

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class SQSPublisher:
    """Async SQS publisher that sends Pydantic models as JSON messages.

    Uses asyncio.to_thread to wrap the synchronous boto3 send_message call,
    matching the pattern used by the Scribe consumer.
    """

    def __init__(
        self,
        sqs_client: Any,
        queue_url: str,
        metrics: Any | None = None,
    ) -> None:
        """Initialize with a boto3 SQS client and target queue URL.

        Args:
            sqs_client: Synchronous boto3 SQS client.
            queue_url: SQS queue URL to publish messages to.
            metrics: Optional SQSPublisherMetrics instance for instrumentation.
        """
        self._sqs_client = sqs_client
        self._queue_url = queue_url
        self._metrics = metrics
        self._queue_name = queue_url.rstrip("/").rsplit("/", 1)[-1]

    async def send(self, message: BaseModel) -> None:
        """Serialize and send a Pydantic model as an SQS message.

        Args:
            message: Pydantic model to serialize and publish.

        Raises:
            Exception: Any boto3 error propagates to the caller.
        """
        message_type = type(message).__name__
        record = (
            self._metrics.start_publish(self._queue_name, message_type)
            if self._metrics
            else None
        )
        try:
            response = await asyncio.to_thread(
                self._sqs_client.send_message,
                QueueUrl=self._queue_url,
                MessageBody=message.model_dump_json(),
            )
            logger.info(
                "published message to SQS",
                queue_url=self._queue_url,
                message_id=response.get("MessageId") if response else None,
                message_type=message_type,
            )
            if record:
                record(True)
        except Exception as e:
            if record:
                record(False, e)
            raise

    def send_sync(self, message: BaseModel) -> None:
        """Serialize and send a Pydantic model as an SQS message synchronously.

        For use in synchronous (non-async) callers. Calls the boto3 client directly
        without asyncio.to_thread wrapping.

        Args:
            message: Pydantic model to serialize and publish.

        Raises:
            Exception: Any boto3 error propagates to the caller.
        """
        message_type = type(message).__name__
        record = (
            self._metrics.start_publish(self._queue_name, message_type)
            if self._metrics
            else None
        )
        try:
            response = self._sqs_client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=message.model_dump_json(),
            )
            logger.info(
                "published message to SQS (sync)",
                queue_url=self._queue_url,
                message_id=response.get("MessageId"),
                message_type=message_type,
            )
            if record:
                record(True)
        except Exception as e:
            if record:
                record(False, e)
            raise
