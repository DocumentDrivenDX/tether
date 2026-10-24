"""A lease-bound last-known journal prefix; uncertainty seals further writes."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from tether.stores.projections import SessionIndex
from tether.types.execution import CommitUnknown, Entry, NewEntry, SessionState
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import IntegrityError, LeaseLost

from .capture import Capture
from .leases import Appended, Prefix, Token

T = TypeVar("T")


class TurnHandle:
    def __init__(self, store: Capture, token: Token, index: SessionIndex, prefix: Prefix) -> None:
        store.root.check_loop()
        self._store, self._token, self._index = store, token, index
        self._prefix = prefix
        self._lock = asyncio.Lock()
        self._poisoned = self._lost = self._closed = False

    @property
    def turn_id(self) -> str:
        return self._token.turn_id

    @property
    def workers(self) -> WorkerLifetime:
        return self._store.root.workers

    @property
    def session(self) -> SessionState:
        self._store.root.check_loop()
        return self._index.state.model_copy(deep=True)

    @property
    def entries(self) -> tuple[Entry, ...]:
        self._store.root.check_loop()
        return tuple(entry.model_copy(deep=True) for entry in self._index.entries)

    def _ready(self) -> None:
        self._store.root.check_loop()
        if self._closed or self._lost:
            raise LeaseLost("lease_lost")
        if self._poisoned:
            raise CommitUnknown("turn_requires_reopen")

    async def _guard(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._ready()
        try:
            value = await operation()
        except (asyncio.CancelledError, CommitUnknown):
            self._poisoned = True
            raise
        except LeaseLost:
            self._lost = True
            raise
        except IntegrityError:
            self._poisoned = True
            raise
        self._ready()
        return value

    def _stage(self, result: Appended) -> tuple[SessionIndex, tuple[Entry, ...]]:
        staged = self._index.clone()
        for entry in result.entries:
            if entry.index < len(staged.entries):
                if staged.entries[entry.index] != entry:
                    raise IntegrityError("turn_prefix_integrity")
            else:
                staged.apply(entry.model_copy(deep=True))
        staged.summary = staged.summary.model_copy(update={"last_seq": result.summary.last_seq})
        if staged.summary != result.summary:
            raise IntegrityError("turn_projection_integrity")
        return staged, tuple(entry.model_copy(deep=True) for entry in result.entries)

    async def _append(
        self, entries: tuple[NewEntry, ...]
    ) -> tuple[SessionIndex, Appended, tuple[Entry, ...]]:
        result = await self._store.commit(self._token, entries, prefix=self._prefix)
        try:
            staged, accepted = await self._store.root.prepare(
                self._token.session_id, lambda: self._stage(result)
            )
            await self._store.heartbeat(self._token)
        except (asyncio.CancelledError, LeaseLost):
            raise
        except Exception as error:
            raise CommitUnknown("turn_projection_unknown") from error
        return staged, result, accepted

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        self._ready()
        copied = tuple(entry.model_copy(deep=True) for entry in entries)
        async with self._lock:
            staged, result, accepted = await self._guard(lambda: self._append(copied))
            if result.prefix is None:
                self._poisoned = True
                raise CommitUnknown("turn_prefix_unknown")
            self._index, self._prefix = staged, result.prefix
            return accepted

    async def heartbeat(self) -> None:
        await self._guard(lambda: self._store.heartbeat(self._token))

    def seal(self) -> None:
        self._store.root.check_loop()
        self._closed = True

    async def aclose(self) -> None:
        self.seal()
        await self._store.release(self._token)
