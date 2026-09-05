"""Scribe message processor — parses JSON and routes to the correct handler."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.datasources.registry import get_source
from common.utils import log_utils

from .errors import MalformedMessageError

logger = log_utils.get_logger(__name__)

if TYPE_CHECKING:
    from .handlers.correlation import CorrelationHandler
    from .handlers.correlation_group import CorrelationGroupHandler
    from .handlers.generic import GenericHandler
    from .handlers.ghe_pr_tracker import GHEPRTrackerHandler

    AnyHandler = (
        GenericHandler
        | GHEPRTrackerHandler
        | CorrelationHandler
        | CorrelationGroupHandler
    )


def _spec_routes(source_type: str) -> frozenset[str]:
    """Derive a specced source's valid message types from its DataSourceSpec.

    A source with an enrichment message model routes both base + enrichment;
    otherwise base-only. incidentio/jira/ghe_pr are registry-driven; the
    remaining hand-wired sources stay literal below until they are specced.
    """
    spec = get_source(source_type)
    if spec.enrichment_message_model is not None:
        return frozenset({"base", "enrichment"})
    return frozenset({"base"})


# Canonical mapping of valid (source_type, message_type) combinations.
# The processor is the single place where this allowlist is enforced — invalid
# combinations are rejected here before any handler is invoked.
#
# To add a new message_type (e.g. "delete", "patch"), add it to the appropriate
# frozenset(s) here and add the corresponding handler method.
VALID_ROUTES: dict[str, frozenset[str]] = {
    "incidentio": _spec_routes("incidentio"),
    "ghe_pr": _spec_routes("ghe_pr"),
    "jira": _spec_routes("jira"),
    "incident_channel_summary": _spec_routes("incident_channel_summary"),
    "ghe_pr_tracker": frozenset({"base"}),
    "correlation": frozenset({"base"}),
    "correlation_group": frozenset({"base"}),
}


@dataclass(frozen=True)
class GroupKey:
    """Routing key that uniquely identifies a handler dispatch path.

    Used by the batcher to group buffered messages before a flush.
    """

    source_type: str
    message_type: str


@dataclass
class ValidatedMessage:
    """A successfully parsed and validated SQS message body."""

    parsed: dict[str, Any]
    source_type: str
    message_type: str


class ScribeProcessor:
    """Routes Scribe SQS messages to the appropriate handler.

    Each message must carry two discriminator fields:
    - source_type: one of the keys in VALID_ROUTES
    - message_type: one of the values in VALID_ROUTES[source_type]

    The processor owns the canonical validation and routing layer for all valid
    (source_type, message_type) combinations. Invalid combinations are rejected
    here before any handler is invoked.
    """

    def __init__(self, handlers: "Mapping[str, AnyHandler]") -> None:
        """Initialize with a map of source_type → handler instance.

        Args:
            handlers: Mapping from source_type strings to handler instances.
        """
        self._handlers = handlers

    def parse_and_validate(self, body: str) -> ValidatedMessage:
        """Parse and validate a raw SQS message body.

        Args:
            body: Raw JSON string from the SQS message.

        Returns:
            ValidatedMessage with the parsed dict, source_type, and message_type.

        Raises:
            MalformedMessageError: If JSON is invalid, discriminator fields are
                missing, source_type is unknown, or message_type is not valid for
                the given source_type.
        """
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            raise MalformedMessageError(f"Invalid JSON: {e}") from e

        if not isinstance(parsed, dict):
            raise MalformedMessageError("Message body must be a JSON object")

        source_type = parsed.get("source_type")
        message_type = parsed.get("message_type")

        if not source_type or not isinstance(source_type, str):
            raise MalformedMessageError(
                f"Missing or invalid 'source_type': {source_type!r}"
            )
        if not message_type or not isinstance(message_type, str):
            raise MalformedMessageError(
                f"Missing or invalid 'message_type': {message_type!r}"
            )

        valid_types = VALID_ROUTES.get(source_type)
        if valid_types is None:
            raise MalformedMessageError(f"Unknown source_type: {source_type!r}")

        if message_type not in valid_types:
            raise MalformedMessageError(
                f"message_type {message_type!r} is not supported for source_type {source_type!r}"
            )

        handler = self._handlers.get(source_type)
        if handler is None:
            raise MalformedMessageError(f"Unknown source_type: {source_type!r}")

        return ValidatedMessage(
            parsed=parsed,
            source_type=source_type,
            message_type=message_type,
        )

    async def dispatch_batch(
        self, key: GroupKey, parsed_list: list[dict[str, Any]]
    ) -> None:
        """Dispatch a batch of pre-validated messages to the appropriate handler.

        Args:
            key: GroupKey identifying the (source_type, message_type) route.
            parsed_list: List of decoded JSON dicts, all sharing the same route.

        Raises:
            Exception: Any exception raised by the handler propagates unchanged
                for the caller to classify as retryable or non-retryable.
        """
        handler = self._handlers[key.source_type]

        logger.info(
            "scribe routing batch",
            source_type=key.source_type,
            message_type=key.message_type,
            batch_size=len(parsed_list),
        )

        if key.message_type == "base":
            await handler.handle_base_batch(parsed_list)
        elif key.message_type == "enrichment":
            await handler.handle_enrichment_batch(parsed_list)

    async def process(self, body: str) -> None:
        """Parse, validate, and dispatch a single raw SQS message body.

        Thin shim over parse_and_validate + dispatch_batch for single-message
        callers (e.g. tests and legacy code paths).

        Args:
            body: Raw JSON string from the SQS message.

        Raises:
            MalformedMessageError: If JSON is invalid, discriminator fields are
                missing, source_type is unknown, or message_type is not valid for
                the given source_type.
            Exception: Any exception raised by the handler propagates unchanged
                for the caller to classify as retryable or non-retryable.
        """
        validated = self.parse_and_validate(body)
        key = GroupKey(
            source_type=validated.source_type, message_type=validated.message_type
        )
        await self.dispatch_batch(key, [validated.parsed])
