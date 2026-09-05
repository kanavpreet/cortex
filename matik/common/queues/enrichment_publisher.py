"""Publisher for enrichment messages to SQS."""

from common.models.enricher_messages import EnrichmentRequest
from common.queues.sqs_client import SQSClient
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class EnrichmentPublisher:
    """Async publisher for enrichment messages. Used by Incident.io and GHE historians.

    Uses the async SQSClient which wraps blocking boto3 calls with asyncio.to_thread.
    """

    def __init__(self, sqs_client: SQSClient, queue_url: str) -> None:
        """Initialize the enrichment publisher.

        Args:
            sqs_client: Async SQS client instance.
            queue_url: URL of the enricher SQS queue.
        """
        self._sqs = sqs_client
        self._queue_url = queue_url

    async def publish(self, message: EnrichmentRequest) -> str | None:
        """Publish an enrichment request to the SQS queue.

        Args:
            message: Generic enrichment request with source_type, entity_id, and content.

        Returns:
            Message ID if successful, None otherwise.
        """
        try:
            response = await self._sqs.send_message(
                self._queue_url, message.model_dump_json()
            )
            msg_id = response.get("MessageId")
            logger.info(
                "Published enrichment",
                source_type=message.source_type,
                entity_id=message.entity_id,
                task_id=message.task_id,
                message_id=msg_id,
            )
            return msg_id
        except Exception:
            logger.exception(
                "Failed to publish enrichment",
                source_type=message.source_type,
                entity_id=message.entity_id,
            )
            return None


class EnrichmentPublisherSync:
    """Synchronous publisher for JIRA historian.

    Uses boto3 directly for synchronous SQS operations.
    """

    def __init__(self, queue_url: str, region: str = "us-east-1") -> None:
        """Initialize the synchronous enrichment publisher.

        Args:
            queue_url: URL of the enricher SQS queue.
            region: AWS region for SQS. Defaults to us-east-1.
        """
        import boto3

        self._queue_url = queue_url
        self._client = boto3.client("sqs", region_name=region)

    def publish(self, message: EnrichmentRequest) -> str | None:
        """Publish an enrichment request to the SQS queue (sync).

        Args:
            message: Generic enrichment request with source_type, entity_id, and content.

        Returns:
            Message ID if successful, None otherwise.
        """
        try:
            response = self._client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=message.model_dump_json(),
            )
            msg_id: str | None = response.get("MessageId")
            logger.info(
                "Published enrichment (sync)",
                source_type=message.source_type,
                entity_id=message.entity_id,
                task_id=message.task_id,
                message_id=msg_id,
            )
            return msg_id
        except Exception:
            logger.exception(
                "Failed to publish enrichment (sync)",
                source_type=message.source_type,
            )
            return None
