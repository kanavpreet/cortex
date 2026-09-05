"""HookRunner — parallel, fire-and-forget execution of PostWriteHooks."""

import asyncio
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel

from common.utils import log_utils

from .base import PostWriteHook

if TYPE_CHECKING:
    from common.metrics.scribe_metrics import ScribeMetrics

logger = log_utils.get_logger(__name__)


class HookRunner:
    """Runs a list of PostWriteHooks in parallel after a successful DB write.

    All hooks are gathered concurrently. Any exception from a hook is caught,
    logged, and metered — it never re-raises. The SQS ack is driven by the
    DAO write outcome only.
    """

    def __init__(
        self,
        hooks: Sequence[PostWriteHook],
        metrics: "ScribeMetrics | None",
    ) -> None:
        self._hooks = list(hooks)
        self._metrics = metrics

    def __bool__(self) -> bool:
        """``True`` iff at least one hook is registered.

        Lets callers skip work that's only needed to feed a hook (e.g. the
        stale-key lookup in ``GenericHandler.handle_base_batch``) when this
        runner is a no-op ``HandlerHooks()`` default.
        """
        return bool(self._hooks)

    async def run_all(self, message: BaseModel) -> None:
        if not self._hooks:
            return

        results = await asyncio.gather(
            *(self._invoke(hook, message) for hook in self._hooks),
            return_exceptions=True,
        )

        for hook, result in zip(self._hooks, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "post-write hook failed",
                    hook=hook.name,
                    exc_info=result,
                )

    async def _invoke(self, hook: PostWriteHook, message: BaseModel) -> None:
        start = time.perf_counter()
        status = "success"
        try:
            await hook.run(message)
        except Exception:
            status = "error"
            raise
        finally:
            if self._metrics:
                duration = time.perf_counter() - start
                self._metrics.record_hook(hook.name, status, duration)
