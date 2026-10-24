"""Process-local actual-worker ownership, scoped to a storage namespace."""

import asyncio
from collections.abc import Callable


class WorkerLifetime:
    """Concrete ownership handle shared by leased logs; never durable evidence."""

    def __init__(self, on_idle: Callable[[], None] | None = None) -> None:
        self.active: dict[asyncio.Future[object], str] = {}
        self.closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self.on_idle = on_idle

    def check_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        if self._loop is not current:
            raise RuntimeError("wrong_event_loop")

    def busy(self, session_id: str) -> bool:
        self.check_loop()
        return session_id in self.active.values()

    def retain(self, session_id: str, task: asyncio.Future[object]) -> None:
        self.check_loop()
        if self.closed:
            raise RuntimeError("worker_lifetime_closed")
        self.active[task] = session_id
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Future[object]) -> None:
        self.active.pop(task, None)
        if not self.active and self.on_idle is not None:
            self.on_idle()
