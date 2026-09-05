"""Shared test doubles for audit test suites."""

from typing import Any

from common.clients.facade_client import FacadeMessage


class _FakeLLMClient:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        self.calls.append({"model": model, "operation": operation})
        return self._response


class _RaisingLLMClient:
    def send_message_with_retry(
        self,
        model: str | None,
        messages: list[FacadeMessage],
        operation: str = "default",
    ) -> str:
        raise RuntimeError("boom")
