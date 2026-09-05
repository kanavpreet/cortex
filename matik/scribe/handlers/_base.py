"""HandlerHooks — composable post-write hook container for Scribe handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scribe.hooks.runner import HookRunner


class HandlerHooks:
    """Holds HookRunners for each message_type a handler supports.

    Pass to any handler constructor to attach post-write side effects.
    Defaults to noop runners so handlers with no hooks need no changes.

    Example:
        hooks = HandlerHooks(base=HookRunner([my_hook], metrics))
        handler = GenericHandler(spec, dao, hooks=hooks)
    """

    def __init__(
        self,
        base: HookRunner | None = None,
        enrichment: HookRunner | None = None,
    ) -> None:
        from scribe.hooks.runner import HookRunner as _Runner

        self.base: HookRunner = base if base is not None else _Runner([], None)
        self.enrichment: HookRunner = (
            enrichment if enrichment is not None else _Runner([], None)
        )
