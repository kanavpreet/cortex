"""PostWriteHook Protocol — interface for all Scribe post-write hooks."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class PostWriteHook(Protocol):
    """A task to run after a successful Scribe DB write.

    Hooks are fire-and-forget: HookRunner catches all exceptions, logs them,
    and emits a metric. They never block or delay the SQS ack.
    Implementations must be idempotent — SQS may redeliver a message and
    re-run the full (DAO + hooks) sequence.
    """

    name: str

    async def run(self, message: BaseModel) -> None: ...
