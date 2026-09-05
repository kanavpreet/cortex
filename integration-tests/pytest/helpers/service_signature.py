"""Phase 1c service-signature auth flow for httpx.

Reimplements the construction from matik/common/utils/service_auth.py
(timestamp + method + path[?query] + sha256(body), HMAC-SHA256, `sha256=`
hex digest) rather than importing it — this test harness intentionally has
no dependency on the `matik` app package (see requirements.txt), so it stays
a small, self-contained copy of the same protocol instead.
"""

import hashlib
import hmac
import time
from collections.abc import Generator

import httpx

_SIGNATURE_PREFIX = "sha256="
SERVICE_TIMESTAMP_HEADER = "X-Matik-Service-Timestamp"
SERVICE_SIGNATURE_HEADER = "X-Matik-Service-Signature"


class ServiceSignatureAuth(httpx.Auth):
    """Attaches Phase 1c signature headers to every outgoing request.

    Signing happens per-request (fresh timestamp, exact body bytes actually
    being sent), so this must be an httpx auth flow rather than a static
    header — a header set once at Client construction time can't include a
    request-specific timestamp or body hash.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        timestamp = str(int(time.time()))
        # raw_path is path + "?" + query exactly as it will go on the wire —
        # signing this instead of just request.url.path means query params
        # are covered too, not just the path and body.
        material = b".".join(
            [
                timestamp.encode(),
                request.method.upper().encode(),
                request.url.raw_path,
                hashlib.sha256(request.content).hexdigest().encode(),
            ]
        )
        digest = hmac.new(self._secret.encode(), material, hashlib.sha256).hexdigest()
        request.headers[SERVICE_TIMESTAMP_HEADER] = timestamp
        request.headers[SERVICE_SIGNATURE_HEADER] = f"{_SIGNATURE_PREFIX}{digest}"
        yield request
