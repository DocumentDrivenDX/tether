"""Process-local actual-worker ownership, scoped to a storage namespace."""

import asyncio
from collections.abc import Callable
from contextvars import Context


class WorkerLifetime:
    """Concrete ownership handle shared by leased logs; never durable evidence."""

    def __init__(self, on_idle: Callable[[], None] | None = None) -> None:
        self.active: dict[asyncio.Future[object], str] = {}
        self.closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self.on_idle = on_idle
        self._retainer: asyncio.TimerHandle | None = None

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
        if task.get_loop() is not asyncio.get_running_loop():
            raise RuntimeError("wrong_event_loop")
        self.check_loop()
        if self.closed:
            raise RuntimeError("worker_lifetime_closed")
        self.active[task] = session_id
        task.add_done_callback(self._finished, context=Context())
        self._retain()

    def _finished(self, task: asyncio.Future[object]) -> None:
        self.active.pop(task, None)
        self._retain()
        if not self.active and self.on_idle is not None:
            self.on_idle()

    def _retain(self) -> None:
        if not self.active:
            if self._retainer is not None:
                self._retainer.cancel()
                self._retainer = None
        elif self._retainer is None and self._loop is not None:
            self._retainer = self._loop.call_later(1, self._tick, context=Context())

    def _tick(self) -> None:
        self._retainer = None
        self._retain()
