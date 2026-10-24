"""Async composition lifetime; resources and identity never come from the environment."""

from __future__ import annotations

import asyncio
from typing import Literal, Self

from tether.engine import Engine
from tether.types.client import ClientLimits
from tether.types.history import HistoryProvider
from tether.types.storage import RecoveredTurn, SessionStore, Unsupported

from .access import SessionAccess
from .feed import Followers


class SessionService:
    def __init__(
        self,
        *,
        engine: Engine,
        store: SessionStore,
        history: HistoryProvider,
        limits: ClientLimits,
        resume: Literal["off", "report", "auto"] = "report",
    ) -> None:
        if resume != "off" and (
            store.capabilities.durability == "none" or not store.capabilities.fencing
        ):
            raise Unsupported("unsupported_store")
        if resume != "off":
            capabilities = history.capabilities
            levels = {"none": 0, "process": 1, "os": 2}
            if (
                not capabilities.writable
                or capabilities.delivery != "immediate"
                or capabilities.durability not in levels
                or levels[capabilities.durability] < levels[store.capabilities.durability]
                or not {"message", "summary", "model_exchange"} <= capabilities.coverage
            ):
                raise Unsupported("unsupported_history_recovery")
        if resume == "auto":
            raise Unsupported("auto_resume_unqualified")
        self.engine, self.store, self.history, self.limits = engine, store, history, limits
        self.access = SessionAccess(store, history, engine.policy, limits)
        self.followers = Followers(self.access)
        self.resume_mode = resume
        self.recovered: tuple[RecoveredTurn, ...] = ()
        self.closed = False
        self.drained = False
        self._cleanup: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self.access.bind()
        try:
            if self.resume_mode != "off":
                self.recovered = await self.store.recover(now=self.engine.clock())
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _finish_close(self) -> None:
        await self.engine.aclose()
        await self.followers.close()
        await self.engine.wait_closed()
        self.drained = await self.store.aclose()

    @staticmethod
    def _observe_cleanup(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def aclose(self) -> bool:
        self.access.check_loop()
        if self.closed:
            return self.drained
        self.access.seal()
        self.closed = True
        try:
            async with asyncio.timeout(self.limits.shutdown_timeout_seconds):
                engine_drained = await self.engine.aclose()
                await self.followers.close()
                if engine_drained:
                    self.drained = await self.store.aclose()
        except TimeoutError:
            self.drained = False
        finally:
            if not self.drained:
                self._cleanup = asyncio.create_task(self._finish_close())
                self._cleanup.add_done_callback(self._observe_cleanup)
        return self.drained
