"""SQS Purger service entry point"""

import sys

import boto3
import botocore.exceptions

from common.config import load_config
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def main() -> None:
    """Purge all configured SQS queues."""
    config = load_config("matik-sqspurger-config.yml")
    log_utils.configure(
        level=config.common.log_level, environment=config.common.environment
    )

    if not config.sqs_purger or not config.sqs_purger.queue_urls:
        logger.info("no queue urls configured, nothing to purge")
        return

    client = boto3.client("sqs", region_name=config.sqs_purger.region)

    failed: list[str] = []
    for url in config.sqs_purger.queue_urls:
        name = url.rsplit("/", 1)[-1]
        try:
            client.purge_queue(QueueUrl=url)
            logger.info("purged queue", queue=name)
        except botocore.exceptions.ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "AWS.SimpleQueueService.PurgeQueueInProgress"
            ):
                logger.info("purge already in progress, skipping", queue=name)
            else:
                logger.exception("failed to purge queue", queue=name)
                failed.append(name)
        except Exception:
            logger.exception("failed to purge queue", queue=name)
            failed.append(name)

    if failed:
        logger.error("some queues failed to purge", failed=failed)
        sys.exit(1)

    logger.info(
        "all queues purged successfully", count=len(config.sqs_purger.queue_urls)
    )
