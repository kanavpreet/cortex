"""Yoyo CallbackEvent Kafka message model + deserialization.

Yoyo (callbacks.airbnb.com) receives inbound webhooks from external providers
and produces them onto Kafka topics. Chronicler subscribes to those topics
directly via confluent-kafka-python (following the bento-box/snake precedent
— there is no off-the-shelf HTTP-forwarding Omnes sidecar at Airbnb).

The on-wire format is a **two-layer Thrift binary envelope** (TBinaryProtocol),
matching what snake_yoyo_consumer.py does:

    Kafka record bytes
      └─ jitney RawMessage (event_v1.thrift):
           field 1 = EventMetadata (skipped)
           field 2 = raw_event: binary  ← contains the CallbackEvent bytes
      └─ Yoyo CallbackEvent (yoyo_v1.thrift):
           field 1  = external_service_type: string
           field 6  = headers: map<string, string>
           field 7  = payload: binary  ← the original webhook body (JSON string)
           field 11 = request_received_ts: i64
           (other fields exist but are not needed by Chronicler)

The `payload` field, once unwrapped, is the original webhook body bytes that
the provider POSTed to Yoyo — typically a JSON document.
"""

from pydantic import BaseModel, Field, ValidationError

from chronicler.models._thrift_binary import (
    T_I64,
    T_MAP,
    T_STRING,
    ThriftDecodeError,
    read_struct,
)


class CallbackEvent(BaseModel):
    """A Yoyo webhook callback event as published to Kafka.

    The `external_service_type` field identifies the webhook provider
    (e.g. "matik_github_webhook_events") and is used to route the event
    to the correct transformer.
    """

    schema_: str | None = Field(
        default=None,
        alias="schema",
        description="Jitney schema identifier (e.g. com.airbnb.jitney.event.yoyo:CallbackEvent:1.0.4).",
    )
    external_service_type: str = Field(
        ...,
        description="Identifies the webhook provider/route (e.g. 'matik_github_webhook_events').",
    )
    http_method: int | None = Field(
        default=None,
        description="HTTP method enum (e.g. 3 = POST).",
    )
    host: str | None = Field(
        default=None,
        description="Hostname the webhook was received on (e.g. callbacks.airbnb.com).",
    )
    path: str | None = Field(
        default=None,
        description="URL path the webhook was received on.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Original HTTP headers from the webhook provider (lowercased by Yoyo).",
    )
    payload: str = Field(
        ...,
        description="Raw webhook body as a JSON string.",
    )
    request_received_ts: int | None = Field(
        default=None,
        description="Unix timestamp in milliseconds when Yoyo received the webhook.",
    )

    model_config = {"populate_by_name": True}

    @property
    def provider(self) -> str:
        """Derive the short provider name from external_service_type.

        Yoyo's external_service_type strings can vary across environments
        (e.g. `matik_sandbox_ghe_webhook_events`, `matik_ghe_webhook_events`).
        Match on the provider token to handle all environment prefixes uniformly.

        Examples:
            matik_sandbox_ghe_webhook_events        -> github
            matik_ghe_webhook_events                 -> github
            matik_github_webhook_events              -> github
            matik_sandbox_jira_webhook_events        -> jira
            matik_sandbox_incidentio_webhook_events  -> incidentio
            matik_generic_webhook_events             -> matik
            matik_sandbox_generic_webhook_events     -> matik

        Note: every external_service_type is prefixed "matik_", so a bare
        "matik" token would ambiguously match all of them — the generic Matik
        webhook provider (OpsBot's incident-channel-summary feed, see
        chronicler/transformers/matik_webhook.py) instead uses the distinct
        "generic" token, per its #api-infra registration:
        matik_generic_webhook_events / matik_sandbox_generic_webhook_events.
        Staging has no separate registration and shares the production
        (non-sandbox) external_service_type.
        """
        # Order matters for unambiguous matches — checked left to right.
        token_to_provider: list[tuple[str, str]] = [
            ("github", "github"),
            ("ghe", "github"),
            ("jira", "jira"),
            ("incidentio", "incidentio"),
            ("generic", "matik"),
        ]
        tokens = set(self.external_service_type.split("_"))
        for token, provider in token_to_provider:
            if token in tokens:
                return provider
        return self.external_service_type


class CallbackEventDeserializationError(Exception):
    """Raised when a Kafka record value cannot be decoded into a CallbackEvent."""


# Field IDs from jitney-schemas IDLs (master branch). Pinned here so a Thrift
# schema bump that re-orders fields will produce a deserialization error rather
# than a silent misread.
_RAW_MESSAGE_FIELD_RAW_EVENT = 2  # binary

_CALLBACK_FIELD_EXTERNAL_SERVICE_TYPE = 1  # string
_CALLBACK_FIELD_HOST = 3  # string
_CALLBACK_FIELD_PATH = 4  # string
_CALLBACK_FIELD_HEADERS = 6  # map<string, string>
_CALLBACK_FIELD_PAYLOAD = 7  # binary
_CALLBACK_FIELD_REQUEST_RECEIVED_TS = 11  # i64
_CALLBACK_FIELD_SCHEMA = 31337  # string


def deserialize_record_value(value: bytes) -> CallbackEvent:
    """Decode a Kafka record value into a CallbackEvent.

    The on-wire format is a two-layer Thrift binary envelope:
    Kafka bytes -> RawMessage -> raw_event bytes -> CallbackEvent.

    Raises:
        CallbackEventDeserializationError: malformed Thrift or missing fields.
    """
    try:
        envelope = read_struct(value)
    except ThriftDecodeError as e:
        raise CallbackEventDeserializationError(
            f"failed to decode RawMessage envelope: {e}"
        ) from e

    raw_event_field = envelope.get(_RAW_MESSAGE_FIELD_RAW_EVENT)
    if raw_event_field is None or raw_event_field[0] != T_STRING:
        raise CallbackEventDeserializationError(
            "RawMessage.raw_event (field 2) missing or not a binary"
        )
    raw_event_bytes: bytes = raw_event_field[1]

    try:
        cb = read_struct(raw_event_bytes)
    except ThriftDecodeError as e:
        raise CallbackEventDeserializationError(
            f"failed to decode CallbackEvent from RawMessage.raw_event: {e}"
        ) from e

    external_service_type = _take_string(cb, _CALLBACK_FIELD_EXTERNAL_SERVICE_TYPE)
    if external_service_type is None:
        raise CallbackEventDeserializationError(
            "CallbackEvent.external_service_type (field 1) missing"
        )

    payload_field = cb.get(_CALLBACK_FIELD_PAYLOAD)
    if payload_field is None or payload_field[0] != T_STRING:
        raise CallbackEventDeserializationError(
            "CallbackEvent.payload (field 7) missing or not a binary"
        )
    payload_bytes: bytes = payload_field[1]
    try:
        payload_str = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CallbackEventDeserializationError(
            f"CallbackEvent.payload is not valid UTF-8: {e}"
        ) from e

    headers_field = cb.get(_CALLBACK_FIELD_HEADERS)
    headers: dict[str, str] = {}
    # read_struct only decodes map<string,string>; other kinds come back as None.
    if (
        headers_field is not None
        and headers_field[0] == T_MAP
        and isinstance(headers_field[1], dict)
    ):
        headers = headers_field[1]

    request_received_ts: int | None = None
    ts_field = cb.get(_CALLBACK_FIELD_REQUEST_RECEIVED_TS)
    if ts_field is not None and ts_field[0] == T_I64:
        request_received_ts = ts_field[1]

    schema_str = _take_string(cb, _CALLBACK_FIELD_SCHEMA)
    host = _take_string(cb, _CALLBACK_FIELD_HOST)
    path = _take_string(cb, _CALLBACK_FIELD_PATH)

    try:
        return CallbackEvent(
            external_service_type=external_service_type,
            headers=headers,
            payload=payload_str,
            request_received_ts=request_received_ts,
            schema=schema_str,
            host=host,
            path=path,
        )
    except ValidationError as e:
        raise CallbackEventDeserializationError(
            f"decoded CallbackEvent does not match Pydantic schema: {e}"
        ) from e


def _take_string(fields: dict[int, tuple[int, object]], field_id: int) -> str | None:
    """Extract a UTF-8 string from a field that may or may not be present."""
    entry = fields.get(field_id)
    if entry is None or entry[0] != T_STRING:
        return None
    raw = entry[1]
    if not isinstance(raw, bytes):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
