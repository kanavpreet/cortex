"""EnigmatologistCorrelationHook — publishes a CorrelationRequest to the enigmatologist SQS queue."""

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class EnigmatologistCorrelationHook:
    """After a successful Incident.io base write, fires a CorrelationRequest to the
    enigmatologist SQS queue so the LLM correlation pipeline can link the new incident
    to related PRs and tickets.

    This is a fire-and-forget hook — failures are swallowed by HookRunner.
    The payload shape matches the CorrelationRequest previously sent from
    POST /v1/incidentio/incident/batch in api/routes/incidentio.py.
    """

    name = "enigmatologist_correlation"

    def __init__(self, sqs_client: Any, queue_url: str) -> None:
        self._sqs = sqs_client
        self._queue_url = queue_url

    async def run(self, message: BaseModel) -> None:
        data = message.model_dump()
        payload = {
            "reference_id": data.get("reference_id"),
            "created_at": data.get("created_at"),
            "description_summary": data.get("description_summary"),
            "affected_services": data.get("affected_services"),
            "incident_channel_summary": data.get("incident_channel_summary"),
        }
        body = json.dumps(payload, default=str)
        await asyncio.to_thread(
            self._sqs.send_message,
            QueueUrl=self._queue_url,
            MessageBody=body,
        )
        logger.info(
            "enigmatologist correlation request sent",
            reference_id=payload.get("reference_id"),
        )
