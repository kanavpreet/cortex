"""Unit tests for the shared content-capture toggle."""

import pytest

from common.llm_tracing.content import set_capture_content, should_capture_content

_ENV = "TRACELOOP_TRACE_CONTENT"


class TestShouldCaptureContent:
    def test_defaults_true_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        assert should_capture_content() is True

    def test_false_when_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "false")
        assert should_capture_content() is False

    def test_case_and_whitespace_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_ENV, "  TRUE ")
        assert should_capture_content() is True


class TestSetCaptureContent:
    def test_sets_env_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        set_capture_content(True)
        assert should_capture_content() is True

    def test_sets_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        set_capture_content(False)
        assert should_capture_content() is False
