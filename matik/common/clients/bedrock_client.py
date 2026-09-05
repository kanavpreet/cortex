"""Bedrock client for interacting with Airbnb's LLM Fusion Hub using AWS Bedrock Converse API."""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.clients.facade_client import (
    FacadeBadRequestError,
    FacadeMessage,
)
from common.llm_tracing import record_bedrock_response, traced_bedrock_call
from common.metrics.client_metrics import ClientMetrics
from common.metrics.facade_metrics import FacadeMetrics
from common.models.bedrock_config import BedrockConfig
from common.models.common_config import CommonConfig
from common.utils import log_utils
from common.utils.env_utils import is_local_environment
from common.utils.retry_utils import get_backoff_delay

logger = log_utils.get_logger(__name__)

__all__ = [
    "BedrockClient",
    "BedrockClientSync",
    "create_bedrock_client",
    "create_bedrock_client_sync",
]


def _build_bedrock_request(
    messages: list[FacadeMessage],
    max_tokens: int,
) -> dict[str, Any]:
    """Convert FacadeMessage list to Bedrock Converse request body.

    System messages are extracted into the top-level 'system' field.
    User and assistant messages are converted to Bedrock content format.

    Args:
        messages: List of FacadeMessage objects.
        max_tokens: Maximum tokens for the inference config.

    Returns:
        Bedrock Converse API request body dict.
    """
    system_blocks = []
    converse_messages = []

    for msg in messages:
        if msg.role == "system":
            system_blocks.append({"text": msg.content})
        else:
            converse_messages.append(
                {"role": msg.role, "content": [{"text": msg.content}]}
            )

    body: dict[str, Any] = {
        "messages": converse_messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_blocks:
        body["system"] = system_blocks

    return body


def _trace_messages(messages: list[FacadeMessage]) -> list[dict[str, str]]:
    """Shape request messages as role/content pairs for trace content capture."""
    return [{"role": m.role, "content": m.content} for m in messages]


def _extract_bedrock_response(data: dict[str, Any]) -> str:
    """Extract text content from a Bedrock Converse API response.

    Args:
        data: Parsed JSON response from the Bedrock Converse API.

    Returns:
        The assistant's response text, or empty string if not found.
    """
    try:
        content = data["output"]["message"]["content"]
        if not content:
            logger.warning("No content blocks returned from Bedrock")
            return ""
        return str(content[0].get("text", ""))
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected Bedrock response structure: %s", data)
        return ""


@dataclass
class BedrockClient:
    """Async client for Bedrock Converse API via Airbnb's LLM Fusion Hub.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = BedrockClient(
            bedrock_config=config.bedrock,
            common_config=config.common,
        )

        messages = [
            facade_system_message("You are a helpful assistant."),
            facade_user_message("What is the capital of France?"),
        ]
        response = await client.send_message(None, messages)
    """

    bedrock_config: BedrockConfig
    common_config: CommonConfig | None = None
    metrics: ClientMetrics | None = None
    facade_metrics: FacadeMetrics | None = None
    _base_url: str = field(init=False)
    _region: str = field(init=False)
    _default_model: str = field(init=False)
    _max_tokens: int = field(init=False)
    _max_retries: int = field(init=False)
    _backoff_delays: list[float] = field(init=False)
    _headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize Bedrock client with configuration."""
        self._base_url = self.bedrock_config.base_url
        self._region = self.bedrock_config.region
        self._default_model = self.bedrock_config.default_model
        self._max_tokens = self.bedrock_config.max_tokens
        self._max_retries = self.bedrock_config.max_retries
        self._backoff_delays = self.bedrock_config.backoff_delays

        is_local = is_local_environment(self.common_config)
        iap_token = os.environ.get("IAP_TOKEN")

        if is_local and not iap_token:
            logger.warning(
                "Running locally but IAP_TOKEN env var not set - requests may fail"
            )

        self._headers = {
            "Content-Type": "application/json",
            "x-llm-aws-region": self._region,
        }
        if is_local and iap_token:
            self._headers["Proxy-Authorization"] = f"Bearer {iap_token}"

        logger.info(
            "Created Bedrock client with base URL: %s, default model: %s",
            self._base_url,
            self._default_model,
        )

    async def send_message(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages to Bedrock Converse API and return the response.

        Args:
            model: Model ID to use, or None to use the default model.
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking.

        Returns:
            The assistant's response content as a string.

        Raises:
            httpx.HTTPStatusError: If the API call returns an HTTP error.
            Exception: If the API call fails for another reason.
        """
        text, _, _ = await self._send_once(model, messages, operation)
        return text

    async def _send_once(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> tuple[str, int, int]:
        """Send a single Bedrock request; return (text, prompt_tokens, completion_tokens)."""
        use_model = model or self._default_model
        url = f"{self._base_url}/model/{use_model}/converse"

        logger.debug(
            "Sending Bedrock Converse request with %d messages using model: %s",
            len(messages),
            use_model,
        )

        body = _build_bedrock_request(messages, self._max_tokens)

        record_facade = None
        if self.facade_metrics:
            record_facade = self.facade_metrics.start_call(use_model, operation)

        try:
            with traced_bedrock_call(
                use_model, operation, _trace_messages(messages)
            ) as span:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(url, headers=self._headers, json=body)
                    response.raise_for_status()
                    data = response.json()

                # Bedrock Converse does not return token usage in the same way;
                # usage fields may vary. Extract if present.
                usage = data.get("usage", {})
                prompt_tokens = usage.get("inputTokens", 0)
                completion_tokens = usage.get("outputTokens", 0)

                result = _extract_bedrock_response(data)
                record_bedrock_response(
                    span, use_model, prompt_tokens, completion_tokens, result
                )

            if record_facade:
                try:
                    record_facade(prompt_tokens, completion_tokens, False)
                except Exception:
                    logger.warning("Failed to record facade metrics")

            logger.info(
                "Successfully received Bedrock Converse response",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            return result, prompt_tokens, completion_tokens
        except Exception as e:
            if record_facade:
                try:
                    record_facade(0, 0, True)
                except Exception:
                    logger.warning("Failed to record facade metrics")
            raise e

    async def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages to Bedrock with automatic retry logic on failures.

        Retries with exponential backoff based on the configured
        max_retries and backoff_delays.
        Default behavior: 3 retries with backoff delays of 10s, 20s, 40s.

        Args:
            model: Model ID to use, or None for default.
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking.

        Returns:
            The assistant's response content as a string.

        Raises:
            FacadeBadRequestError: If the API returns a 400 error (not retriable).
            Exception: If all retry attempts fail.
        """
        text, _, _ = await self.send_message_with_usage(model, messages, operation)
        return text

    async def send_message_with_usage(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> tuple[str, int, int]:
        """Send messages with retry logic, returning text and token counts.

        Returns:
            Tuple of (response_text, prompt_tokens, completion_tokens).
        """
        last_err: Exception | None = None
        start_time = time.perf_counter()

        for attempt in range(self._max_retries):
            try:
                response = await self._send_once(model, messages, operation)
                if attempt > 0:
                    logger.info(
                        "Successfully sent Bedrock message after %d retry attempts",
                        attempt,
                    )
                if self.metrics:
                    duration = time.perf_counter() - start_time
                    use_model = model or self._default_model
                    self.metrics.record_request(
                        "POST", f"model/{use_model}/converse", duration, 200
                    )
                return response
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 400:
                    reason = err.response.text
                    logger.warning(
                        "Bedrock request failed with 400, not retrying: %s", reason
                    )
                    raise FacadeBadRequestError(reason) from err
                last_err = err

                if self.metrics:
                    use_model = model or self._default_model
                    self.metrics.record_retry(
                        "POST", f"model/{use_model}/converse", attempt + 1
                    )

                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Bedrock request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    await asyncio.sleep(delay)
            except Exception as err:
                last_err = err

                if self.metrics:
                    use_model = model or self._default_model
                    self.metrics.record_retry(
                        "POST", f"model/{use_model}/converse", attempt + 1
                    )

                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Bedrock request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    await asyncio.sleep(delay)

        if self.metrics:
            duration = time.perf_counter() - start_time
            use_model = model or self._default_model
            self.metrics.record_request(
                "POST", f"model/{use_model}/converse", duration, 500, last_err
            )

        logger.error(
            "Bedrock request failed after %d attempts, giving up: %s",
            self._max_retries,
            last_err,
        )
        if last_err:
            raise last_err
        raise RuntimeError(
            "Unexpected error: no exception captured but all retries failed"
        )

    def send_message_sync(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Synchronous wrapper for send_message.

        Use this when you need to call from synchronous code.
        """
        return asyncio.run(self.send_message(model, messages, operation))

    def send_message_with_retry_sync(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Synchronous wrapper for send_message_with_retry.

        Use this when you need to call from synchronous code.
        """
        return asyncio.run(self.send_message_with_retry(model, messages, operation))


class BedrockClientSync:
    """Synchronous Bedrock Converse client using httpx for simpler sync workflows.

    Use this when you don't need async functionality.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = BedrockClientSync(
            bedrock_config=config.bedrock,
            common_config=config.common,
        )

        messages = [
            facade_system_message("You are a helpful assistant."),
            facade_user_message("What is the capital of France?"),
        ]
        response = client.send_message(None, messages)
    """

    def __init__(
        self,
        bedrock_config: BedrockConfig,
        common_config: CommonConfig | None = None,
        facade_metrics: FacadeMetrics | None = None,
    ) -> None:
        """Initialize the synchronous Bedrock client.

        Args:
            bedrock_config: Bedrock configuration from MatikConfig.bedrock
            common_config: Common configuration from MatikConfig.common (optional)
            facade_metrics: FacadeMetrics for LLM call and token tracking (optional)
        """
        self.facade_metrics = facade_metrics
        self._base_url = bedrock_config.base_url
        self._region = bedrock_config.region
        self._default_model = bedrock_config.default_model
        self._max_tokens = bedrock_config.max_tokens
        self._max_retries = bedrock_config.max_retries
        self._backoff_delays = bedrock_config.backoff_delays

        is_local = is_local_environment(common_config)
        iap_token = os.environ.get("IAP_TOKEN")

        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-llm-aws-region": self._region,
        }
        if is_local and iap_token:
            self._headers["Proxy-Authorization"] = f"Bearer {iap_token}"

        logger.info("Created sync Bedrock client with base URL: %s", self._base_url)

    def send_message(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages to Bedrock Converse API and return the response.

        Args:
            model: Model ID to use, or None to use the default model.
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking.

        Returns:
            The assistant's response content as a string.
        """
        use_model = model or self._default_model
        url = f"{self._base_url}/model/{use_model}/converse"
        body = _build_bedrock_request(messages, self._max_tokens)

        record_facade = None
        if self.facade_metrics:
            record_facade = self.facade_metrics.start_call(use_model, operation)

        try:
            with traced_bedrock_call(
                use_model, operation, _trace_messages(messages)
            ) as span:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(url, headers=self._headers, json=body)
                    response.raise_for_status()
                    data = response.json()

                usage = data.get("usage", {})
                prompt_tokens = usage.get("inputTokens", 0)
                completion_tokens = usage.get("outputTokens", 0)

                result = _extract_bedrock_response(data)
                record_bedrock_response(
                    span, use_model, prompt_tokens, completion_tokens, result
                )

            if record_facade:
                try:
                    record_facade(prompt_tokens, completion_tokens, False)
                except Exception:
                    logger.warning("Failed to record facade metrics")

            logger.info(
                "Successfully received Bedrock Converse response",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            return result
        except Exception as e:
            if record_facade:
                try:
                    record_facade(0, 0, True)
                except Exception:
                    logger.warning("Failed to record facade metrics")
            raise e

    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages with retry logic.

        Args:
            model: Model ID to use, or None for default.
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking.

        Returns:
            The assistant's response content as a string.

        Raises:
            FacadeBadRequestError: If the API returns a 400 error (not retriable).
            Exception: If all retry attempts fail.
        """
        last_err: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                return self.send_message(model, messages, operation)
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 400:
                    reason = err.response.text
                    logger.warning(
                        "Bedrock request failed with 400, not retrying: %s", reason
                    )
                    raise FacadeBadRequestError(reason) from err
                last_err = err
                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Bedrock request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    time.sleep(delay)
            except Exception as err:
                last_err = err
                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Bedrock request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    time.sleep(delay)

        if last_err:
            raise last_err
        raise RuntimeError("Unexpected error in retry logic")


def create_bedrock_client(
    bedrock_config: BedrockConfig,
    common_config: CommonConfig | None = None,
    metrics: ClientMetrics | None = None,
    facade_metrics: FacadeMetrics | None = None,
) -> BedrockClient:
    """Create an async Bedrock client from configuration objects.

    Args:
        bedrock_config: BedrockConfig from MatikConfig.bedrock
        common_config: CommonConfig from MatikConfig.common (optional)
        metrics: ClientMetrics for request instrumentation (optional)
        facade_metrics: FacadeMetrics for LLM call and token tracking (optional)

    Returns:
        Configured BedrockClient instance (async).

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_bedrock_client(
            bedrock_config=config.bedrock,
            common_config=config.common,
        )

        response = await client.send_message(None, messages)
    """
    return BedrockClient(
        bedrock_config=bedrock_config,
        common_config=common_config,
        metrics=metrics,
        facade_metrics=facade_metrics,
    )


def create_bedrock_client_sync(
    bedrock_config: BedrockConfig,
    common_config: CommonConfig | None = None,
    facade_metrics: FacadeMetrics | None = None,
) -> BedrockClientSync:
    """Create a sync Bedrock client from configuration objects.

    Args:
        bedrock_config: BedrockConfig from MatikConfig.bedrock
        common_config: CommonConfig from MatikConfig.common (optional)
        facade_metrics: FacadeMetrics for LLM call and token tracking (optional)

    Returns:
        Configured BedrockClientSync instance (synchronous).

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_bedrock_client_sync(
            bedrock_config=config.bedrock,
            common_config=config.common,
        )

        response = client.send_message(None, messages)
    """
    return BedrockClientSync(
        bedrock_config=bedrock_config,
        common_config=common_config,
        facade_metrics=facade_metrics,
    )
