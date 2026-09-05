"""Matik API client for interacting with the Matik api service."""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.models.api_config import ApiConfig
from common.utils.retry_utils import get_backoff_delay
from common.utils.service_auth import build_signature_headers

logger = logging.getLogger(__name__)

# Retry configuration
DEFAULT_BACKOFF_DELAYS = [2.0, 4.0, 8.0]
MAX_RETRIES = 3


def _is_retryable_status_code(status_code: int) -> bool:
    """Check if a status code should trigger a retry.

    Retries on 5xx server errors and 429 rate limiting.
    """
    return status_code >= 500 or status_code == 429


@dataclass
class MatikApiClient:
    """Client for interacting with the Matik api/API service.

    Includes automatic retry with exponential backoff for transient failures.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = MatikApiClient(api_config=config.api)

        response = client.get_request("/api/v1/entities")
        response = client.post_json_request("/api/v1/analyze", {"text": "..."})
    """

    api_config: ApiConfig
    backoff_delays: list[float] = field(default_factory=lambda: DEFAULT_BACKOFF_DELAYS)
    default_headers: dict[str, str] = field(default_factory=dict)

    _url: str = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the Matik API client."""
        self._url = self.api_config.api_endpoint.rstrip("/")
        logger.info("Created Matik API client for endpoint: %s", self._url)

    def _build_url(self, path: str) -> str:
        """Construct a properly formatted URL by joining the base URL with the path.

        Handles cases where base URL may or may not have trailing slash
        and path may or may not have leading slash.
        """
        clean_path = path.lstrip("/")
        return f"{self._url}/{clean_path}"

    def _signature_headers(
        self, method: str, target: str, body: bytes
    ) -> dict[str, str]:
        """Compute Phase 1c service-signature headers, or {} if unconfigured.

        `target` must be byte-identical to what the server sees: a leading
        slash, and — for GETs with query params — `?` + the exact raw query
        string that will be sent (see get_request, which builds this from
        the already-encoded httpx.URL rather than reconstructing it here).
        """
        if not self.api_config.service_secret:
            return {}
        signed_target = "/" + target.lstrip("/")
        return build_signature_headers(
            self.api_config.service_secret, method, signed_target, body
        )

    def json_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Send an HTTP request with a JSON body to the specified endpoint.

        Includes automatic retry with exponential backoff for transient failures.

        Args:
            method: HTTP method (POST, PUT, PATCH, DELETE, etc.)
            path: API endpoint path (e.g., "/api/v1/analyze")
            body: JSON body to send (dict or list)
            headers: Additional headers to include in the request

        Returns:
            Response body as bytes.

        Raises:
            httpx.HTTPStatusError: If the request fails after all retries.
        """
        url = self._build_url(path)
        method_upper = method.upper()
        # Body is serialized once, up front, so the exact bytes we sign are
        # the exact bytes sent — signing over `json=body` risks httpx's
        # encoder producing slightly different bytes than ours. ensure_ascii
        # is disabled to match httpx's own encoder, which sends UTF-8 bytes
        # rather than \uXXXX-escaping them.
        body_bytes = (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
        )
        last_err: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            # Signature headers (incl. timestamp) are recomputed fresh on
            # every attempt rather than reused from before the loop — this
            # keeps each attempt an exact stand-in for a brand-new request
            # and doesn't rely on today's backoff delays (<=14s total)
            # staying well under the signature's skew window
            # (service_auth.DEFAULT_MAX_SKEW_SECONDS, currently 120s).
            request_headers = {
                "Content-Type": "application/json",
                **self._signature_headers(method_upper, path, body_bytes),
                **self.default_headers,
            }
            if headers:
                request_headers.update(headers)

            if attempt > 0:
                delay = get_backoff_delay(attempt - 1, self.backoff_delays)
                logger.warning(
                    "Retrying %s to %s (attempt %d/%d) after %.1fs",
                    method_upper,
                    url,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)

            logger.info(
                "%s to URL: %s (attempt %d/%d)",
                method_upper,
                url,
                attempt + 1,
                MAX_RETRIES + 1,
            )

            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.request(
                        method_upper,
                        url,
                        content=body_bytes,
                        headers=request_headers,
                    )

                    # Log the response status
                    self._log_response(url, method_upper, response.status_code)

                    # Check if we should retry based on status code
                    if _is_retryable_status_code(response.status_code):
                        last_err = httpx.HTTPStatusError(
                            f"Request to {url} failed with status code {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        logger.warning(
                            "Retryable status %d (attempt %d/%d)",
                            response.status_code,
                            attempt + 1,
                            MAX_RETRIES + 1,
                        )
                        continue

                    # Non-retryable error (4xx except 429)
                    response.raise_for_status()

                    # Success
                    if attempt > 0:
                        logger.info(
                            "%s to %s succeeded after %d retries",
                            method_upper,
                            url,
                            attempt,
                        )

                    return response.content

            except httpx.HTTPStatusError:
                raise
            except Exception as err:
                last_err = err
                logger.warning(
                    "%s request failed (attempt %d/%d): %s",
                    method_upper,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    err,
                )

        logger.error(
            "%s to %s failed after %d attempts", method_upper, url, MAX_RETRIES + 1
        )
        if last_err:
            raise last_err
        raise RuntimeError(
            f"{method_upper} to {url} failed after {MAX_RETRIES + 1} attempts"
        )

    def post_json_request(
        self,
        path: str,
        body: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Send a POST request with a JSON body to the specified endpoint.

        Convenience wrapper around json_request() for POST requests.

        Args:
            path: API endpoint path (e.g., "/api/v1/analyze")
            body: JSON body to send (dict or list)
            headers: Additional headers to include in the request

        Returns:
            Response body as bytes.

        Raises:
            httpx.HTTPStatusError: If the request fails after all retries.
        """
        return self.json_request("POST", path, body=body, headers=headers)

    def get_request(
        self,
        path: str,
        params: dict[str, str | list[str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Send a GET request to the specified endpoint with optional query parameters.

        Includes automatic retry with exponential backoff for transient failures.

        Args:
            path: API endpoint path (e.g., "/api/v1/entities")
            params: Query parameters
            headers: Additional headers to include in the request

        Returns:
            Response body as bytes.

        Raises:
            httpx.HTTPStatusError: If the request fails after all retries.
        """
        # Query params are baked into the URL up front (rather than passed
        # separately to client.get) so the signature can be computed over
        # the exact path+query bytes that will go on the wire.
        url = httpx.URL(self._build_url(path), params=params or {})
        last_err: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            # Signature headers (incl. timestamp) are recomputed fresh on
            # every attempt rather than reused from before the loop — this
            # keeps each attempt an exact stand-in for a brand-new request
            # and doesn't rely on today's backoff delays (<=14s total)
            # staying well under the signature's skew window
            # (service_auth.DEFAULT_MAX_SKEW_SECONDS, currently 120s).
            request_headers = {
                **self._signature_headers("GET", url.raw_path.decode(), b""),
                **self.default_headers,
            }
            if headers:
                request_headers.update(headers)

            if attempt > 0:
                delay = get_backoff_delay(attempt - 1, self.backoff_delays)
                logger.warning(
                    "Retrying GET to %s (attempt %d/%d) after %.1fs",
                    url,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)

            logger.info(
                "Getting URL: %s (attempt %d/%d)", url, attempt + 1, MAX_RETRIES + 1
            )

            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=request_headers or None)

                    # Log the response status
                    self._log_response(str(url), "GET", response.status_code)

                    # Check if we should retry based on status code
                    if _is_retryable_status_code(response.status_code):
                        last_err = httpx.HTTPStatusError(
                            f"Request to {url} failed with status code {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        logger.warning(
                            "Retryable status %d (attempt %d/%d)",
                            response.status_code,
                            attempt + 1,
                            MAX_RETRIES + 1,
                        )
                        continue

                    # Non-retryable error (4xx except 429)
                    response.raise_for_status()

                    # Success
                    logger.debug("Successfully read response body")

                    if attempt > 0:
                        logger.info(
                            "GET to %s succeeded after %d retries", url, attempt
                        )

                    return response.content

            except httpx.HTTPStatusError:
                raise
            except Exception as err:
                last_err = err
                logger.warning(
                    "GET request failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    err,
                )

        logger.error("GET to %s failed after %d attempts", url, MAX_RETRIES + 1)
        if last_err:
            raise last_err
        raise RuntimeError(f"GET to {url} failed after {MAX_RETRIES + 1} attempts")

    def _log_response(self, url: str, method: str, status_code: int) -> None:
        """Log the response from the API service based on the status code."""
        if 200 <= status_code <= 226:
            logger.info("Request success: %s %s -> %d", method, url, status_code)
        else:
            logger.error("Request failed: %s %s -> %d", method, url, status_code)


def create_matik_api_client(
    api_config: ApiConfig,
    backoff_delays: list[float] | None = None,
    default_headers: dict[str, str] | None = None,
) -> MatikApiClient:
    """Create a Matik API client from configuration objects.

    Args:
        api_config: ApiConfig from MatikConfig.api
        backoff_delays: Custom backoff delays for retries (optional)
        default_headers: Headers to include in all requests (optional)

    Returns:
        Configured MatikApiClient instance.

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_matik_api_client(api_config=config.api)
    """
    return MatikApiClient(
        api_config=api_config,
        backoff_delays=backoff_delays or DEFAULT_BACKOFF_DELAYS,
        default_headers=default_headers or {},
    )
