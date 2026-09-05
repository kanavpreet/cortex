"""Kafka consumer loop for Chronicler.

Subscribes directly to Yoyo callback Kafka topics via confluent-kafka-python,
following the bento-box/snake precedent. Yoyo already validated HMAC
signatures before publishing, so no signature check is performed here.

For each message:
    1. Deserialize the CallbackEvent envelope.
    2. Resolve the provider transformer via the `provider` property.
    3. Parse the inner webhook payload (JSON string in CallbackEvent.payload).
    4. Filter via transformer.should_process().
    5. Publish a sanitized base message to Scribe SQS.
    6. Publish an EnrichmentRequest to the Enricher SQS (if applicable).
    7. Commit the offset only after both publishes succeed (at-least-once).
       Scribe is upsert-safe, so duplicate base messages on retry are harmless.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, cast

from chronicler.models.callback_event import (
    CallbackEvent,
    CallbackEventDeserializationError,
    deserialize_record_value,
)
from chronicler.transformers.base import get_transformer
from common.models.chronicler_config import ChroniclerConfig, ChroniclerKafkaConfig
from common.utils import log_utils

if TYPE_CHECKING:
    from common.clients.sqs_publisher import SQSPublisher
    from common.metrics.chronicler_metrics import ChroniclerMetrics
    from common.queues.enrichment_publisher import EnrichmentPublisher

logger = log_utils.get_logger(__name__)


class _KafkaMessage(Protocol):
    """Subset of confluent_kafka.Message used by the loop."""

    def value(self) -> bytes | None: ...
    def topic(self) -> str | None: ...
    def partition(self) -> int | None: ...
    def offset(self) -> int | None: ...
    def error(self) -> object | None: ...


class _KafkaConsumer(Protocol):
    """Subset of confluent_kafka.Consumer used by the loop. Lets tests inject fakes."""

    def subscribe(self, topics: list[str]) -> None: ...
    def poll(self, timeout: float) -> _KafkaMessage | None: ...
    def commit(self, message: _KafkaMessage, asynchronous: bool = ...) -> None: ...
    def close(self) -> None: ...


def build_consumer(config: ChroniclerKafkaConfig) -> _KafkaConsumer:
    """Build a confluent_kafka.Consumer from chronicler kafka config.

    Imported lazily so that test environments without librdkafka installed can
    still import this module and inject a fake consumer.

    The real Consumer's `commit` signature is wider than the Protocol declares
    (overloaded keyword-only forms), so we cast through Any.
    """
    from confluent_kafka import Consumer

    consumer = Consumer(
        {
            "bootstrap.servers": config.bootstrap_servers,
            "group.id": config.group_id,
            "auto.offset.reset": config.auto_offset_reset,
            "enable.auto.commit": False,
        }
    )
    return cast(_KafkaConsumer, cast(Any, consumer))


class KafkaConsumerLoop:
    """Long-running Kafka consumer that routes events to provider transformers."""

    def __init__(
        self,
        consumer: _KafkaConsumer,
        kafka_config: ChroniclerKafkaConfig,
        scribe_publisher: SQSPublisher,
        enrichment_publisher: EnrichmentPublisher | None,
        chronicler_config: ChroniclerConfig | None = None,
        metrics: ChroniclerMetrics | None = None,
    ) -> None:
        self._consumer = consumer
        self._kafka_config = kafka_config
        self._scribe = scribe_publisher
        self._enricher = enrichment_publisher
        # chronicler_config carries per-provider HMAC secrets. Defaults to a
        # fresh empty config so tests can omit it without disabling other paths.
        self._chronicler_config = chronicler_config or ChroniclerConfig()
        self._metrics = metrics
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the loop to drain in-flight work and exit."""
        if not self._shutdown.is_set():
            logger.info("Shutdown requested")
            self._shutdown.set()

    async def run(self) -> None:
        """Subscribe and consume until shutdown is requested."""
        self._consumer.subscribe(self._kafka_config.topics)
        logger.info(
            "Kafka consumer subscribed",
            topics=self._kafka_config.topics,
            group_id=self._kafka_config.group_id,
        )
        try:
            while not self._shutdown.is_set():
                poll_start = time.perf_counter()
                msg = await asyncio.to_thread(
                    self._consumer.poll, self._kafka_config.poll_timeout_seconds
                )
                if self._metrics is not None:
                    self._metrics.record_kafka_poll(time.perf_counter() - poll_start)
                if msg is None:
                    continue
                err = msg.error()
                if err:
                    logger.warning("Kafka message error", error=str(err))
                    if self._metrics is not None:
                        self._metrics.record_kafka_error(type(err).__name__)
                    continue
                await self._handle_message(msg)
        finally:
            self._consumer.close()
            logger.info("Kafka consumer closed")

    async def _handle_message(self, msg: _KafkaMessage) -> None:
        """Process a single Kafka record and commit on success."""
        topic = msg.topic() or "<unknown>"
        partition = msg.partition()
        offset = msg.offset()

        task_id = log_utils.generate_task_id(__name__)
        log_utils.set_task_id(task_id)

        record_done: Callable[[str, str], None] | None = (
            self._metrics.start_message(topic) if self._metrics is not None else None
        )
        if self._metrics is not None:
            self._metrics.record_received(topic)

        provider = "unknown"
        status = "error"
        try:
            raw_value = msg.value()
            if not raw_value:
                logger.warning("Empty Kafka message, skipping", topic=topic)
                self._consumer.commit(msg, asynchronous=False)
                status = "malformed_envelope"
                return

            try:
                event = deserialize_record_value(raw_value)
            except CallbackEventDeserializationError as e:
                logger.error(
                    "Failed to deserialize CallbackEvent, skipping",
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    error=str(e),
                )
                self._consumer.commit(msg, asynchronous=False)
                status = "malformed_envelope"
                return

            provider, status = await self._process_event(
                event, topic, partition, offset, task_id
            )
            self._consumer.commit(msg, asynchronous=False)
        finally:
            if record_done is not None:
                record_done(provider, status)
            log_utils.clear_task_id()

    async def _process_event(
        self,
        event: CallbackEvent,
        topic: str,
        partition: int | None,
        offset: int | None,
        task_id: str,
    ) -> tuple[str, str]:
        """Process one CallbackEvent. Returns ``(provider, terminal_status)``."""
        provider = event.provider

        try:
            transformer = get_transformer(provider)
        except KeyError:
            logger.warning(
                "Unknown provider, discarding",
                provider=provider,
                external_service_type=event.external_service_type,
                topic=topic,
            )
            return provider, "unknown_provider"

        # HMAC validation. Yoyo passes webhooks through unverified — Chronicler
        # is the trust boundary. If the provider has no signature scheme at all
        # (transformer.webhook_secret_key == "" — e.g. Jira webhooks, which
        # Atlassian does not sign), or no secret is configured (sandbox bootstrap
        # before secret-lair entry exists), skip the check entirely. Otherwise
        # drop the event on any mismatch.
        secret_key = transformer.webhook_secret_key
        secret = (
            getattr(self._chronicler_config, secret_key, None) if secret_key else None
        )
        if not secret_key:
            if self._metrics is not None:
                self._metrics.record_signature(provider, "skipped_no_scheme")
        elif not secret:
            if self._metrics is not None:
                self._metrics.record_signature(provider, "skipped_no_secret")
        else:
            body_bytes = event.payload.encode("utf-8")
            if not transformer.validate_signature(body_bytes, event.headers, secret):
                logger.warning(
                    "Invalid HMAC signature, discarding",
                    provider=provider,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                )
                if self._metrics is not None:
                    self._metrics.record_signature(provider, "invalid")
                return provider, "signature_failed"
            if self._metrics is not None:
                self._metrics.record_signature(provider, "valid")

        try:
            payload = json.loads(event.payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to parse inner webhook payload, discarding",
                provider=provider,
                topic=topic,
                partition=partition,
                offset=offset,
            )
            return provider, "malformed_payload"

        # Headers from Yoyo are lowercased. Transformer event_type_header uses
        # canonical casing; look up case-insensitively.
        event_type = _get_header_ci(event.headers, transformer.event_type_header)

        if not transformer.should_process(event_type, payload):
            logger.info(
                "Event filtered out",
                provider=provider,
                event_type=event_type,
                topic=topic,
            )
            return provider, "filtered"

        base_message = transformer.to_base_message(payload)
        if base_message is not None:
            try:
                await self._scribe.send(base_message)
            except BaseException:
                if self._metrics is not None:
                    self._metrics.record_publish("scribe", provider, False)
                raise
            if self._metrics is not None:
                self._metrics.record_publish("scribe", provider, True)

        enrichment_request = transformer.to_enrichment_request(payload, task_id)
        if enrichment_request is not None:
            # Optional async hook: lets a transformer fetch extra content the
            # webhook payload omits (e.g. GHE PR conversation comments). Failure
            # must not block the base enrichment, so we log and proceed.
            augment = getattr(transformer, "augment_enrichment_content", None)
            if augment is not None:
                try:
                    extra_content = await augment(payload)
                except Exception:
                    logger.warning(
                        "Enrichment content augmentation failed; "
                        "proceeding with base content",
                        provider=provider,
                        task_id=task_id,
                        exc_info=True,
                    )
                    extra_content = {}
                if extra_content:
                    enrichment_request.content.update(extra_content)
        if enrichment_request and self._enricher:
            try:
                msg_id = await self._enricher.publish(enrichment_request)
            except BaseException:
                if self._metrics is not None:
                    self._metrics.record_publish("enricher", provider, False)
                raise
            if msg_id is None:
                # EnrichmentPublisher swallows the underlying exception and
                # returns None. Raise here so the offset is NOT committed and
                # the next poll redelivers — Scribe is upsert-safe.
                if self._metrics is not None:
                    self._metrics.record_publish("enricher", provider, False)
                raise RuntimeError(
                    f"EnrichmentPublisher.publish returned None for task_id={task_id}"
                )
            if self._metrics is not None:
                self._metrics.record_publish("enricher", provider, True)

        logger.info(
            "Callback processed",
            provider=provider,
            task_id=task_id,
            topic=topic,
            partition=partition,
            offset=offset,
            source_type=transformer.source_type,
        )
        return provider, "success"


def _get_header_ci(headers: dict[str, str], key: str) -> str:
    """Case-insensitive header lookup. Yoyo lowercases all header keys."""
    if key in headers:
        return headers[key]
    lower = key.lower()
    for k, v in headers.items():
        if k.lower() == lower:
            return v
    return ""


def install_signal_handlers(loop: KafkaConsumerLoop) -> None:
    """Bind SIGTERM/SIGINT to graceful shutdown on the current asyncio loop."""
    asyncio_loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio_loop.add_signal_handler(sig, loop.request_shutdown)


__all__ = [
    "KafkaConsumerLoop",
    "build_consumer",
    "install_signal_handlers",
]
