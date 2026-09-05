"""httpx client wrapper for UAT API contract tests."""

import httpx

from helpers.service_signature import ServiceSignatureAuth

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3


def build_client(
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    service_secret: str | None = None,
) -> httpx.Client:
    """
    Build a synchronous httpx Client with the given base URL and timeout.

    Transport uses a retry strategy for transient failures. When
    service_secret is set, every request is signed per Phase 1c
    (see helpers/service_signature.py) — required once matik-api enforces
    the check, harmless before that.
    """
    transport = httpx.HTTPTransport(retries=DEFAULT_MAX_RETRIES)
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        headers=headers or {},
        transport=transport,
        follow_redirects=False,
        auth=ServiceSignatureAuth(service_secret) if service_secret else None,
    )
