"""Shared HMAC signing/verification for service-to-service Matik API calls.

This is the Phase 1c application-layer check from the access-posture doc: a
second, mesh-independent proof that a request to matik-api actually came
from one of our own services (historian/enricher/enigmatologist/mcp/
integration-tests), not just from whatever AirMesh's `allows` list happened
to admit.

Mirrors the HMAC-SHA256 construction Chronicler already uses for inbound
webhooks (see chronicler/transformers/matik_webhook.py: raw body,
HMAC-SHA256, `sha256=` hex digest, `hmac.compare_digest`), but adds a
timestamp to the signed material. A webhook delivery is a one-shot event;
this secures a live, repeatedly-called service, so a bare body signature
would be replayable indefinitely by anything that ever observed one request.
"""

import hashlib
import hmac
import time
from typing import Any

import structlog

from common.constants import SERVICE_SIGNATURE_HEADER, SERVICE_TIMESTAMP_HEADER

_SIGNATURE_PREFIX = "sha256="

# How far a request's timestamp may drift from "now" (either direction)
# before it's rejected. Bounds the replay window without requiring closely
# synchronized clocks between services.
DEFAULT_MAX_SKEW_SECONDS = 120


class SignatureVerificationError(Exception):
    """Raised by verify_request() with a short, machine-readable reason.

    The reason string is meant for logs/metrics (e.g. "signature_mismatch"),
    not for the caller — callers never learn *why* verification failed.
    """


def _signing_material(timestamp: str, method: str, path: str, body: bytes) -> bytes:
    """Canonical string that gets HMAC'd.

    Binding method + path (not just the body) means a signature captured for
    one route can't be replayed against a different route within the same
    validity window. `path` includes the query string when the request has
    one (see the `path` docstring on sign() for the exact format), so query
    parameters are covered too — not just the path and body.
    """
    return b".".join(
        [
            timestamp.encode(),
            method.upper().encode(),
            path.encode(),
            hashlib.sha256(body).hexdigest().encode(),
        ]
    )


def sign(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    *,
    timestamp: str | None = None,
) -> tuple[str, str]:
    """Compute a (timestamp, signature) pair for a request.

    Args:
        secret: Shared service secret (ApiConfig.service_secret).
        method: HTTP method, e.g. "GET" / "POST".
        path: Request target: path, plus `?` + the raw query string when
            the request has one, e.g. "/v1/jira/issues?start_time=...". Must
            be byte-identical to what the receiver parses on the other end
            (no trailing-slash drift, no re-ordering/re-encoding of query
            params) — on the client, build this from the URL that will
            actually be sent (e.g. httpx.URL(...).raw_path) rather than
            reconstructing it by hand, and on the server, from the raw
            request target (e.g. ASGI `scope["query_string"]`), not from a
            parsed/re-serialized query dict.
        body: Raw request body bytes (b"" for GET/bodyless requests).
        timestamp: Unix epoch seconds as a string; defaults to now. Passed
            explicitly by verify_request() to recompute the expected
            signature for a given incoming timestamp.

    Returns:
        (timestamp, "sha256=<hex digest>").
    """
    ts = timestamp if timestamp is not None else str(int(time.time()))
    digest = hmac.new(
        secret.encode(), _signing_material(ts, method, path, body), hashlib.sha256
    ).hexdigest()
    return ts, f"{_SIGNATURE_PREFIX}{digest}"


def build_signature_headers(
    secret: str, method: str, path: str, body: bytes
) -> dict[str, str]:
    """Sign a request and return the headers to attach to it."""
    timestamp, signature = sign(secret, method, path, body)
    return {
        SERVICE_TIMESTAMP_HEADER: timestamp,
        SERVICE_SIGNATURE_HEADER: signature,
    }


def verify_request(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    *,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> None:
    """Verify a signed request. Raises SignatureVerificationError on failure.

    Fails closed on anything unexpected (missing headers, malformed
    timestamp, stale timestamp, bad signature) rather than returning a
    boolean — callers should treat any exception here the same way,
    matching the fail-closed posture used elsewhere in this design.
    """
    if not timestamp_header or not signature_header:
        raise SignatureVerificationError("missing_headers")

    try:
        timestamp_seconds = int(timestamp_header)
    except ValueError as err:
        raise SignatureVerificationError("malformed_timestamp") from err

    if abs(time.time() - timestamp_seconds) > max_skew_seconds:
        raise SignatureVerificationError("timestamp_out_of_window")

    _, expected_signature = sign(secret, method, path, body, timestamp=timestamp_header)
    if not hmac.compare_digest(signature_header, expected_signature):
        raise SignatureVerificationError("signature_mismatch")


def log_signature_rejection_401(
    logger_: structlog.stdlib.BoundLogger, context: str, **extra: Any
) -> None:
    """Log a distinct, loud message for a 401 caused by our own rejected signature.

    Callers of matik-api (historian/enricher/enigmatologist) must not fold
    this into a generic "failed to fetch" warning: a 401 here means
    matik-api rejected our service signature (missing/stale
    api.service_secret, or clock skew), not that there's genuinely no data.
    Folded into a generic warning, it looks indistinguishable from "no
    results" and can go unnoticed indefinitely once enforcement is turned on
    for an environment.
    """
    logger_.error(
        f"matik-api rejected service signature (401) {context} — check "
        "api.service_secret configuration",
        **extra,
    )
