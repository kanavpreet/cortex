"""Chronicler service configuration models."""

from sqlmodel import Field, SQLModel


class ChroniclerKafkaConfig(SQLModel):
    """Kafka consumer configuration for Chronicler.

    Chronicler consumes Yoyo callback events directly from Kafka brokers via
    AirMesh, following the bento-box/snake precedent. There is no off-the-shelf
    Omnes HTTP-forwarding sidecar at Airbnb for non-JVM services.
    """

    bootstrap_servers: str = Field(
        default="",
        description="Kafka bootstrap servers (AirMesh host:port). e.g. 'kafka-prod-a.kafka-prod-a:9092'.",
    )
    group_id: str = Field(
        default="matik-chronicler",
        description="Kafka consumer group id. Suffix with environment in config (e.g. matik-chronicler-prod).",
    )
    topics: list[str] = Field(
        default_factory=list,
        description=(
            "Kafka topics to subscribe to. Each topic carries Yoyo CallbackEvents "
            "for one provider; the provider is resolved at runtime from the event's "
            "external_service_type, not the topic name. Names are environment-specific "
            "(e.g. yoyo.callback.matik_sandbox_ghe_webhook_events for sandbox)."
        ),
    )
    auto_offset_reset: str = Field(
        default="latest",
        description="Where to start reading when no committed offset exists. 'latest' matches snake's behavior.",
    )
    poll_timeout_seconds: float = Field(
        default=1.0,
        description="Per-poll timeout for the consumer. Lower = more responsive to SIGTERM, higher = lower CPU.",
    )


class ChroniclerConfig(SQLModel):
    """Configuration for the Chronicler Kafka ingestion service.

    Loaded from kubegen YAML config. Controls the Kafka consumer, SQS queue
    URLs, and per-provider HMAC secrets used to validate webhook signatures
    before forwarding events to Scribe / Enricher.
    """

    kafka: ChroniclerKafkaConfig = Field(
        default_factory=ChroniclerKafkaConfig,
        description="Kafka consumer settings.",
    )
    # Per-provider HMAC secrets. Yoyo does NOT validate webhook signatures —
    # it is Chronicler's responsibility to verify before publishing downstream.
    # A None or empty value means signature validation is disabled for that
    # provider (use only during sandbox bootstrap; staging/prod must set these).
    github_webhook_secret: str | None = Field(
        default=None,
        description="HMAC-SHA256 secret used to validate the X-Hub-Signature-256 header on GHE webhooks.",
    )
    incidentio_webhook_secret: str | None = Field(
        default=None,
        description=(
            "Svix endpoint secret (whsec_...) used to verify Incident.io "
            "webhook signatures. Verified by the official `svix` Python SDK."
        ),
    )
    generic_webhook_secret: str | None = Field(
        default=None,
        description=(
            "HMAC-SHA256 secret used to validate the X-Matik-Signature-256 "
            "header on the generic Matik webhook (external_service_type "
            "matik_generic_webhook_events / matik_sandbox_generic_webhook_events, "
            "e.g. OpsBot's incident-channel-summary feed)."
        ),
    )
    scribe_queue_url: str = Field(
        default="",
        description="SQS URL for the Scribe high-priority queue (base messages).",
    )
    scribe_queue_region: str = Field(
        default="us-east-1",
        description="AWS region for the Scribe SQS queue.",
    )
    enricher_queue_url: str | None = Field(
        default=None,
        description="SQS URL for the Enricher queue (enrichment requests).",
    )
    enricher_queue_region: str | None = Field(
        default=None,
        description="AWS region for the Enricher SQS queue. Defaults to scribe_queue_region.",
    )

    @property
    def resolved_enricher_region(self) -> str:
        return self.enricher_queue_region or self.scribe_queue_region
