"""Tests for HookRunner."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from scribe.hooks.runner import HookRunner


class _FakeModel(BaseModel):
    value: str = "test"


class TestHookRunnerEmpty:
    @pytest.mark.asyncio
    async def test_empty_hooks_is_noop(self) -> None:
        """run_all with no hooks completes without error."""
        runner = HookRunner([], None)
        await runner.run_all(_FakeModel())


class TestHookRunnerSuccess:
    @pytest.mark.asyncio
    async def test_all_hooks_called_in_parallel(self) -> None:
        """All hooks are awaited; each receives the same message."""
        msg = _FakeModel(value="hello")
        hook_a = MagicMock(name="hook_a")
        hook_a.name = "hook_a"
        hook_a.run = AsyncMock()
        hook_b = MagicMock(name="hook_b")
        hook_b.name = "hook_b"
        hook_b.run = AsyncMock()

        runner = HookRunner([hook_a, hook_b], None)
        await runner.run_all(msg)

        hook_a.run.assert_awaited_once_with(msg)
        hook_b.run.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self) -> None:
        """record_hook is called with 'success' when hook completes normally."""
        hook = MagicMock(name="good_hook")
        hook.name = "good_hook"
        hook.run = AsyncMock()
        metrics = MagicMock()

        runner = HookRunner([hook], metrics)
        await runner.run_all(_FakeModel())

        metrics.record_hook.assert_called_once()
        args = metrics.record_hook.call_args[0]
        assert args[0] == "good_hook"
        assert args[1] == "success"


class TestHookRunnerFailure:
    @pytest.mark.asyncio
    async def test_failing_hook_is_swallowed(self) -> None:
        """An exception from a hook is caught — run_all does not raise."""
        hook = MagicMock(name="bad_hook")
        hook.name = "bad_hook"
        hook.run = AsyncMock(side_effect=RuntimeError("boom"))

        runner = HookRunner([hook], None)
        await runner.run_all(_FakeModel())  # must not raise

    @pytest.mark.asyncio
    async def test_failing_hook_logs_warning(self) -> None:
        """A failing hook emits a warning log."""
        hook = MagicMock(name="bad_hook")
        hook.name = "bad_hook"
        hook.run = AsyncMock(side_effect=ValueError("oops"))

        runner = HookRunner([hook], None)
        with patch("scribe.hooks.runner.logger") as mock_logger:
            await runner.run_all(_FakeModel())
            mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_failing_hook_metrics_recorded_as_error(self) -> None:
        """record_hook is called with 'error' when the hook raises."""
        hook = MagicMock(name="bad_hook")
        hook.name = "bad_hook"
        hook.run = AsyncMock(side_effect=RuntimeError("fail"))
        metrics = MagicMock()

        runner = HookRunner([hook], metrics)
        await runner.run_all(_FakeModel())

        metrics.record_hook.assert_called_once()
        args = metrics.record_hook.call_args[0]
        assert args[0] == "bad_hook"
        assert args[1] == "error"

    @pytest.mark.asyncio
    async def test_one_failing_hook_does_not_cancel_sibling(self) -> None:
        """When one hook fails, other hooks in the same run_all still execute."""
        bad_hook = MagicMock(name="bad")
        bad_hook.name = "bad"
        bad_hook.run = AsyncMock(side_effect=RuntimeError("fail"))

        good_hook = MagicMock(name="good")
        good_hook.name = "good"
        good_hook.run = AsyncMock()

        runner = HookRunner([bad_hook, good_hook], None)
        await runner.run_all(_FakeModel())

        good_hook.run.assert_awaited_once()
