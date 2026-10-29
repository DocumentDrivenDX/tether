"""Async composition lifetime; resources and identity never come from the environment."""

from __future__ import annotations

import asyncio
from contextvars import Context
from typing import Literal, Self

from tether.engine import Engine
from tether.types import AttemptIdentity
from tether.types.archive import ArchiveSink, ArchiveSource
from tether.types.client import ClientLimits
from tether.types.execution import Actor, copy_actor
from tether.types.history import HistoryProvider
from tether.types.storage import RecoveredTurn, SessionStore, Unsupported

from .access import SessionAccess
from .archived import ArchiveAccess
from .commands import CommandTurns
from .feed import Followers
from .turns import OwnedTurns


class SessionService:
    def __init__(
        self,
        *,
        engine: Engine,
        store: SessionStore,
        history: HistoryProvider,
        limits: ClientLimits | None = None,
        resume: Literal["off", "report", "auto"] = "report",
        default_spec: str | None = None,
        archive: ArchiveSource | None = None,
        archive_sink: ArchiveSink | None = None,
    ) -> None:
        limits = limits if limits is not None else ClientLimits()
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
        self.access = SessionAccess(
            store, history, engine.policy, limits, observers=engine.observers
        )
        self.archives = ArchiveAccess(self.access, archive, archive_sink)
        self.followers = Followers(self.access)
        previous_live_text = engine.live_text
        previous_live_done = engine.live_done
        self._previous_live_text = previous_live_text
        self._previous_live_done = previous_live_done

        async def publish_live_text(
            owner: str, identity: AttemptIdentity, offset: int, text: str
        ) -> None:
            await self.followers.publish_live(owner, identity, offset, text)
            if previous_live_text is not None:
                await previous_live_text(owner, identity, offset, text)

        self._service_live_text = publish_live_text
        engine.live_text = self._service_live_text

        async def finish_live_text(
            owner: str,
            identity: AttemptIdentity,
            reason: Literal["outage", "cancelled", "reconnect"] | None,
        ) -> None:
            self.followers.finish_live(owner, identity, reason)
            if previous_live_done is not None:
                await previous_live_done(owner, identity, reason)

        self._service_live_done = finish_live_text
        engine.live_done = self._service_live_done
        self.turns = OwnedTurns(self.access, engine, default_spec)
        self.commands = CommandTurns(self.turns)
        self.resume_mode = resume
        self.recovered: tuple[RecoveredTurn, ...] = ()
        self.closed = False
        self.drained = False
        self._cleanup: asyncio.Task[None] | None = None

    async def archive_session(self, actor: Actor, session_id: str) -> None:
        await self.archives.write(copy_actor(actor), session_id)

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
        await self.engine.aclose_execution()
        await self.turns.wait_closed()
        await self.followers.close()
        await self.engine.wait_closed()
        physical = await self.store.aclose()
        await self.engine.aclose_observers()
        await self.engine.wait_observers_closed()
        self.drained = physical

    def _restore_live_callbacks(self) -> None:
        if self.engine.live_text is self._service_live_text:
            self.engine.live_text = self._previous_live_text
        if self.engine.live_done is self._service_live_done:
            self.engine.live_done = self._previous_live_done

    @staticmethod
    def _observe_cleanup(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def aclose(self) -> bool:
        self.access.check_loop()
        if self.closed:
            return self.drained
        self.access.seal()
        self.turns.seal()
        self.closed = True
        self._restore_live_callbacks()
        deadline = asyncio.get_running_loop().time() + self.limits.shutdown_timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                await self.engine.aclose_execution(deadline=deadline)
                await self.turns.wait_closed()
                await self.followers.close()
                await self.engine.wait_closed()
                physical = await self.store.aclose()
                observed = await self.engine.aclose_observers(deadline=deadline)
                self.drained = physical and observed
        except TimeoutError:
            self.drained = False
        finally:
            if not self.drained:
                self._cleanup = asyncio.create_task(self._finish_close(), context=Context())
                self._cleanup.add_done_callback(self._observe_cleanup)
        return self.drained
