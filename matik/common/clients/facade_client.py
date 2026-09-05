"""Facade client for interacting with Airbnb's LLM Fusion Hub using OpenAI SDK."""

import asyncio
import os
import time
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI, BadRequestError

from common.metrics.client_metrics import ClientMetrics
from common.metrics.facade_metrics import FacadeMetrics
from common.models.common_config import CommonConfig
from common.models.facade_config import FacadeConfig
from common.utils import log_utils
from common.utils.env_utils import is_local_environment
from common.utils.retry_utils import get_backoff_delay

logger = log_utils.get_logger(__name__)


class FacadeClientError(Exception):
    """Base class for all Facade client errors."""


class FacadeBadRequestError(FacadeClientError):
    """Raised when facade returns 400 Bad Request (not retriable)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Bad request: {reason}")


class FacadeContentFilteredError(FacadeClientError):
    """Raised when the LLM response was blocked by a content filter.

    Occurs when the Facade returns a choice with finish_reason='content_filter'.
    This is a non-retriable error — the same prompt will always be filtered.
    """


@dataclass
class FacadeMessage:
    """Chat message with a role and content for the Facade client."""

    role: str  # "system", "user", or "assistant"
    content: str


def facade_system_message(content: str) -> FacadeMessage:
    """Create a system message that sets the AI's behavior/instructions.

    Example: facade_system_message("You are a helpful incident analyst")
    """
    return FacadeMessage(role="system", content=content)


def facade_user_message(content: str) -> FacadeMessage:
    """Create a user message with a question or prompt.

    Example: facade_user_message("What is the status of INC-123?")
    """
    return FacadeMessage(role="user", content=content)


def facade_assistant_message(content: str) -> FacadeMessage:
    """Create an assistant message with a previous AI response.

    Used for maintaining conversation history/context.
    Example: facade_assistant_message("INC-123 is currently active")
    """
    return FacadeMessage(role="assistant", content=content)


@dataclass
class FacadeClient:
    """Client for interacting with Airbnb's LLM Fusion Hub using OpenAI SDK.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = FacadeClient(
            facade_config=config.facade,
            common_config=config.common,
        )

        messages = [
            facade_system_message("You are a helpful assistant."),
            facade_user_message("What is the capital of France?"),
        ]
        response = await client.send_message("gpt-4o", messages)
    """

    facade_config: FacadeConfig
    common_config: CommonConfig | None = None
    metrics: ClientMetrics | None = None
    facade_metrics: FacadeMetrics | None = None
    _client: AsyncOpenAI = field(init=False)
    _default_model: str = field(init=False)
    _max_retries: int = field(init=False)
    _backoff_delays: list[float] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the OpenAI client with Facade configuration."""
        base_url = self.facade_config.base_url
        resource_bucket = self.facade_config.resource_bucket
        self._default_model = self.facade_config.default_model
        api_version = self.facade_config.api_version

        is_local = is_local_environment(self.common_config)

        # IAP token: only needed for local development (from env var)
        iap_token = os.environ.get("IAP_TOKEN")
        if is_local and not iap_token:
            logger.warning(
                "Running locally but IAP_TOKEN env var not set - requests may fail"
            )

        self._max_retries = self.facade_config.max_retries
        self._backoff_delays = self.facade_config.backoff_delays

        # Build default headers
        default_headers: dict[str, str] = {
            "x-azure-resource-bucket": resource_bucket,
            "x-azure-region": "global",
        }
        if is_local and iap_token:
            default_headers["Proxy-Authorization"] = f"Bearer {iap_token}"

        # Build default query parameters
        default_query: dict[str, str] = {"api-version": api_version}

        logger.info("Creating Facade client with base URL: %s", base_url)

        # Create OpenAI client configured for Facade endpoints
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key="not-needed",  # API key not used; auth via AirMesh or IAP
            default_headers=default_headers,
            default_query=default_query,
        )

        logger.info("Created Facade client with default model: %s", self._default_model)

    async def send_message(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages to Facade and return the assistant's response.

        Args:
            model: Model to use. If empty, uses the default model.
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking (e.g., "summarize_root_cause").

        Returns:
            The assistant's response content as a string.

        Raises:
            Exception: If the API call fails.

        Example - Simple query:
            messages = [
                facade_system_message("You are a helpful assistant."),
                facade_user_message("What is the capital of France?"),
            ]
            response = await client.send_message("gpt-4o", messages)

        Example - Conversation with context:
            messages = [
                facade_system_message("You are an incident analyst."),
                facade_user_message("What's the status of INC-123?"),
                facade_assistant_message("INC-123 is currently active."),
                facade_user_message("Who's working on it?"),
            ]
            response = await client.send_message("gpt-4o", messages)
        """
        text, _, _ = await self._send_once(model, messages, operation)
        return text

    async def _send_once(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> tuple[str, int, int]:
        """Send a single request and return (text, prompt_tokens, completion_tokens)."""
        use_model = model or self._default_model

        logger.debug(
            "Sending chat completion request with %d messages using model: %s",
            len(messages),
            use_model,
        )

        openai_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        record_facade = None
        if self.facade_metrics:
            record_facade = self.facade_metrics.start_call(use_model, operation)

        try:
            raw = await self._client.chat.completions.with_raw_response.create(
                model=use_model,
                messages=openai_messages,  # type: ignore[arg-type]
            )
            completion = raw.parse()

            # Record rate limit headers
            if self.facade_metrics:
                try:
                    self.facade_metrics.record_rate_limits(
                        raw.headers, use_model, operation
                    )
                except Exception:
                    logger.warning("Failed to record rate limit metrics")

            # Extract token usage from completion
            prompt_tokens = 0
            completion_tokens = 0
            if completion.usage:
                prompt_tokens = completion.usage.prompt_tokens or 0
                completion_tokens = completion.usage.completion_tokens or 0

            # Record facade metrics
            if record_facade:
                try:
                    record_facade(prompt_tokens, completion_tokens, False)
                except Exception:
                    logger.warning("Failed to record facade metrics")

            if not completion.choices:
                logger.warning("No choices returned from LLM")
                return "", prompt_tokens, completion_tokens

            finish_reason = completion.choices[0].finish_reason
            if finish_reason == "content_filter":
                logger.warning(
                    "LLM response blocked by content filter",
                    model=use_model,
                    operation=operation,
                )
                raise FacadeContentFilteredError(
                    f"Response blocked by content filter for operation '{operation}'"
                )

            response = completion.choices[0].message.content or ""
            logger.info(
                "Successfully received chat completion response",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            return response, prompt_tokens, completion_tokens
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
        """Send messages to Facade with automatic retry logic on failures.

        Retries with exponential backoff based on the configured
        max_retries and backoff_delays.
        Default behavior: 3 retries with backoff delays of 10s, 20s, 40s.

        Args:
            model: Model to use (empty string uses default model).
            messages: List of FacadeMessage objects to send.
            operation: Operation name for metrics tracking (e.g., "summarize_root_cause").

        Returns:
            The assistant's response content as a string.

        Raises:
            Exception: If all retry attempts fail.

        Example:
            messages = [
                facade_system_message("You are a helpful assistant."),
                facade_user_message("Summarize this text"),
            ]
            response = await client.send_message_with_retry("gpt-4o", messages)
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
            Tuple of (response_text, prompt_tokens, completion_tokens)
        """
        last_err: Exception | None = None
        start_time = time.perf_counter()

        for attempt in range(self._max_retries):
            try:
                result = await self._send_once(model, messages, operation)
                if attempt > 0:
                    logger.info(
                        "Successfully sent message after %d retry attempts", attempt
                    )
                if self.metrics:
                    duration = time.perf_counter() - start_time
                    self.metrics.record_request(
                        "POST", "chat/completions", duration, 200
                    )
                return result
            except BadRequestError as err:
                reason = str(err)
                logger.warning(
                    "Facade request failed with 400, not retrying: %s", reason
                )
                raise FacadeBadRequestError(reason) from err
            except FacadeContentFilteredError:
                raise
            except Exception as err:
                last_err = err

                if self.metrics:
                    self.metrics.record_retry("POST", "chat/completions", attempt + 1)

                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Facade request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted - record failed request
        if self.metrics:
            duration = time.perf_counter() - start_time
            self.metrics.record_request(
                "POST", "chat/completions", duration, 500, last_err
            )

        logger.error(
            "Facade request failed after %d attempts, giving up: %s",
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


class FacadeClientSync:
    """Synchronous Facade client using httpx for simpler sync workflows.

    Use this when you don't need async functionality.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = FacadeClientSync(
            facade_config=config.facade,
            common_config=config.common,
        )

        messages = [
            facade_system_message("You are a helpful assistant."),
            facade_user_message("What is the capital of France?"),
        ]
        response = client.send_message("gpt-4o", messages)
    """

    def __init__(
        self,
        facade_config: FacadeConfig,
        common_config: CommonConfig | None = None,
        facade_metrics: FacadeMetrics | None = None,
    ) -> None:
        """Initialize the synchronous Facade client.

        Args:
            facade_config: Facade configuration from MatikConfig.facade
            common_config: Common configuration from MatikConfig.common (optional)
            facade_metrics: FacadeMetrics for LLM call and token tracking (optional)
        """
        self.facade_metrics = facade_metrics
        self._base_url = facade_config.base_url
        resource_bucket = facade_config.resource_bucket
        self._default_model = facade_config.default_model
        api_version = facade_config.api_version

        is_local = is_local_environment(common_config)

        # IAP token: only needed for local development (from env var)
        iap_token = os.environ.get("IAP_TOKEN")

        self._max_retries = facade_config.max_retries
        self._backoff_delays = facade_config.backoff_delays

        # Build default headers
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-azure-resource-bucket": resource_bucket,
            "x-azure-region": "global",
        }
        if is_local and iap_token:
            self._headers["Proxy-Authorization"] = f"Bearer {iap_token}"

        # Build default query parameters
        self._params: dict[str, str] = {"api-version": api_version}

        logger.info("Created sync Facade client with base URL: %s", self._base_url)

    def send_message(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        """Send messages to Facade and return the assistant's response.

        Args:
            model: Model to use (e.g., "gpt-4o"), or None for default
            messages: List of messages to send
            operation: Operation name for metrics tracking (default: "default")

        Returns:
            The assistant's response content as a string
        """
        text, _, _ = self._send_once(model, messages, operation)
        return text

    def _send_once(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> tuple[str, int, int]:
        """Send a single request and return (text, prompt_tokens, completion_tokens)."""
        use_model = model or self._default_model

        # Start metrics recording if enabled
        record_facade = None
        if self.facade_metrics:
            record_facade = self.facade_metrics.start_call(use_model, operation)

        openai_messages = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        url = f"{self._base_url}/chat/completions"

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    url,
                    headers=self._headers,
                    params=self._params,
                    json={"model": use_model, "messages": openai_messages},
                )
                response.raise_for_status()

                # Record rate limit headers
                if self.facade_metrics:
                    try:
                        self.facade_metrics.record_rate_limits(
                            response.headers, use_model, operation
                        )
                    except Exception:
                        logger.warning("Failed to record rate limit metrics")

                data = response.json()

            # Extract token usage from response
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            # Record successful call
            if record_facade:
                try:
                    record_facade(prompt_tokens, completion_tokens, False)
                except Exception:
                    logger.warning("Failed to record facade metrics")

            choices = data.get("choices", [])
            if not choices:
                logger.warning("No choices returned from LLM")
                return "", prompt_tokens, completion_tokens

            content = choices[0].get("message", {}).get("content", "")

            logger.info(
                "Successfully received chat completion response",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

            return str(content) if content else "", prompt_tokens, completion_tokens
        except Exception as e:
            # Record failed call
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
            model: Model to use (e.g., "gpt-4o"), or None for default
            messages: List of messages to send
            operation: Operation name for metrics tracking (default: "default")

        Returns:
            The assistant's response content as a string
        """
        text, _, _ = self.send_message_with_usage(model, messages, operation)
        return text

    def send_message_with_usage(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> tuple[str, int, int]:
        """Send messages with retry logic, returning text and token counts.

        Returns:
            Tuple of (response_text, prompt_tokens, completion_tokens)
        """
        last_err: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                return self._send_once(model, messages, operation)
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 400:
                    # 400 errors are not retriable - raise immediately
                    reason = err.response.text
                    logger.warning(
                        "Facade request failed with 400, not retrying: %s", reason
                    )
                    raise FacadeBadRequestError(reason) from err
                # Other HTTP errors - continue with retry logic
                last_err = err
                if attempt < self._max_retries - 1:
                    delay = get_backoff_delay(attempt, self._backoff_delays)
                    logger.warning(
                        "Facade request failed (attempt %d/%d), retrying in %.1fs: %s",
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
                        "Facade request failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        err,
                    )
                    time.sleep(delay)

        if last_err:
            raise last_err
        raise RuntimeError("Unexpected error in retry logic")


@dataclass
class MockFacadeClient:
    """Mock Facade client for local testing. Returns canned LLM responses without network calls.

    Implements the same method signatures as FacadeClient (both async and sync)
    so the union type FacadeClient | MockFacadeClient is fully compatible.
    """

    async def send_message(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        logger.info("[MOCK] Facade call skipped", model=model, operation=operation)
        return f"[MOCK] Generated summary for operation={operation}"

    async def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        return await self.send_message(model, messages, operation)

    def send_message_sync(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        logger.info("[MOCK] Facade sync call skipped", model=model, operation=operation)
        return f"[MOCK] Generated summary for operation={operation}"

    def send_message_with_retry_sync(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        return self.send_message_sync(model, messages, operation)


def create_facade_client(
    facade_config: FacadeConfig,
    common_config: CommonConfig | None = None,
    metrics: ClientMetrics | None = None,
    facade_metrics: FacadeMetrics | None = None,
) -> FacadeClient | MockFacadeClient:
    """Create an async Facade client from configuration objects.

    Args:
        facade_config: FacadeConfig from MatikConfig.facade
        common_config: CommonConfig from MatikConfig.common (optional)
        metrics: ClientMetrics for request instrumentation (optional)
        facade_metrics: FacadeMetrics for LLM call and token tracking (optional)

    Returns:
        Configured FacadeClient instance (async).

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_facade_client(
            facade_config=config.facade,
            common_config=config.common,
        )

        response = await client.send_message("gpt-4o", messages)
    """
    if facade_config.mock_mode:
        logger.info("Using MockFacadeClient (mock_mode=True)")
        return MockFacadeClient()
    return FacadeClient(
        facade_config=facade_config,
        common_config=common_config,
        metrics=metrics,
        facade_metrics=facade_metrics,
    )


def create_facade_client_sync(
    facade_config: FacadeConfig,
    common_config: CommonConfig | None = None,
    facade_metrics: FacadeMetrics | None = None,
) -> FacadeClientSync:
    """Create a sync Facade client from configuration objects.

    Args:
        facade_config: FacadeConfig from MatikConfig.facade
        common_config: CommonConfig from MatikConfig.common (optional)
        facade_metrics: FacadeMetrics for LLM call and token tracking (optional)

    Returns:
        Configured FacadeClientSync instance (synchronous).

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_facade_client_sync(
            facade_config=config.facade,
            common_config=config.common,
        )

        response = client.send_message("gpt-4o", messages)
    """
    return FacadeClientSync(
        facade_config=facade_config,
        common_config=common_config,
        facade_metrics=facade_metrics,
    )
