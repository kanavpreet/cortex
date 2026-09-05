"""Enricher message processor — parse, validate, and dispatch to handler."""

import json
from typing import Any

from pydantic import ValidationError

from common.models.enricher_config import EnricherConfig
from common.models.enricher_messages import EnrichmentRequest
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


class EnricherProcessor:
    """Parses raw SQS message bodies and dispatches them to the EnrichmentHandler.

    Responsible for:
      - Deserializing JSON message bodies into EnrichmentRequest models.
      - Validating that the source_type is configured in source_mappings.
      - Delegating to the handler for actual LLM enrichment.

    Any parse or schema failure raises MalformedMessageError (non-retryable),
    so the consumer loop will route the message to the DLQ immediately.
    """

    def __init__(self, handler: Any, config: EnricherConfig) -> None:
        """Initialize the processor with an enrichment handler and config.

        Args:
            handler: EnrichmentHandler instance that performs LLM enrichment.
            config: EnricherConfig used to validate incoming source_type values.
        """
        self._handler = handler
        self._config = config

    async def process(self, message_body: str) -> None:
        """Parse a raw SQS message body and dispatch to the handler.

        Steps:
          1. Parse JSON body into an EnrichmentRequest.
          2. Validate source_type is present in config.source_mappings.
          3. Delegate to handler.enrich(request).

        Args:
            message_body: Raw JSON string from the SQS message Body field.

        Raises:
            MalformedMessageError: If the body is not valid JSON or fails
                EnrichmentRequest schema validation, or if source_type is
                not configured in source_mappings.
            Exception: Any exception from handler.enrich() propagates as-is
                for the consumer loop to classify.
        """
        from enricher.exceptions import MalformedMessageError

        # Parse JSON
        try:
            data = json.loads(message_body)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in SQS message body", error=str(e))
            raise MalformedMessageError(f"Invalid JSON: {e}") from e

        # Validate against EnrichmentRequest schema
        try:
            request = EnrichmentRequest.model_validate(data)
        except (ValidationError, Exception) as e:
            logger.warning("EnrichmentRequest schema validation failed", error=str(e))
            raise MalformedMessageError(f"Schema validation failed: {e}") from e

        # Validate source_type is configured
        if request.source_type not in self._config.source_mappings:
            logger.warning(
                "Unknown source_type, no mapping configured",
                source_type=request.source_type,
                configured=list(self._config.source_mappings.keys()),
            )
            raise MalformedMessageError(
                f"No mapping configured for source_type '{request.source_type}'"
            )

        await self._handler.enrich(request)
