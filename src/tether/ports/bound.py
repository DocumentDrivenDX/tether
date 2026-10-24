"""Non-owning explicit caller scopes for owner-oriented storage adapters."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal

from tether.types.execution import Actor, Entry, ExecutionDenied, copy_actor
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import (
    ChangeFeed,
    EffectPage,
    EntryPage,
    Page,
    RecoveredTurn,
    SessionArchive,
    SessionInit,
    SessionPatch,
    SessionQuery,
    SessionSnapshot,
    SessionStore,
    SessionSummary,
    SessionTurnLog,
    SessionView,
    StoreCapabilities,
    Unsupported,
)


class BoundReads:
    def __init__(self, store: SessionStore, actor: Actor) -> None:
        store.workers.check_loop()
        self._store, self._actor = store, copy_actor(actor)
        self._closed = False

    def _owner(self, owner: str) -> None:
        self._store.workers.check_loop()
        if self._closed:
            raise RuntimeError("store_scope_closed")
        if owner != self._actor.id:
            raise ExecutionDenied("denied")

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._store.capabilities

    @property
    def workers(self) -> WorkerLifetime:
        return self._store.workers

    async def get(self, session_id: str, owner: str) -> SessionView | None:
        self._owner(owner)
        return await self._store.get(session_id, owner)

    async def list(
        self, query: SessionQuery, *, cursor: str | None = None, limit: int = 50
    ) -> Page[SessionSummary]:
        self._owner(query.owner)
        return await self._store.list(query.model_copy(deep=True), cursor=cursor, limit=limit)

    async def find(self, owner: str, ref: str) -> SessionSummary | None:
        self._owner(owner)
        return await self._store.find(owner, ref)

    async def effects(
        self, session_id: str, owner: str, *, start: int = 0, limit: int = 50
    ) -> EffectPage:
        self._owner(owner)
        return await self._store.effects(session_id, owner, start=start, limit=limit)

    async def watermark(self, owner: str) -> int:
        self._owner(owner)
        return await self._store.watermark(owner)

    def changes(
        self, owner: str, *, session_id: str | None = None, since: int | None = None
    ) -> ChangeFeed:
        self._owner(owner)
        return self._store.changes(owner, session_id=session_id, since=since)

    async def lookup(
        self,
        session_id: str,
        owner: str,
        *,
        kind: Literal["key", "invocation", "model_call", "turn"],
        value: str,
        limit: int = 1000,
        max_bytes: int | None = None,
    ) -> tuple[Entry, ...]:
        self._owner(owner)
        return await self._store.lookup(
            session_id, owner, kind=kind, value=value, limit=limit, max_bytes=max_bytes
        )

    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot:
        self._owner(owner)
        return await self._store.snapshot(session_id, owner)

    async def entries(
        self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool = False
    ) -> EntryPage:
        self._owner(owner)
        return await self._store.entries(
            session_id, owner, start=start, limit=limit, reverse=reverse
        )


class ActorStore(BoundReads):
    def for_actor(self, actor: Actor) -> SessionStore:
        self._owner(actor.id)
        return self._store.for_actor(copy_actor(actor))

    async def aclose(self) -> bool:
        self._store.workers.check_loop()
        self._closed = True
        return True

    async def create(self, owner: str, init: SessionInit) -> SessionSummary:
        self._owner(owner)
        return await self._store.create(owner, init.model_copy(deep=True))

    async def update(self, session_id: str, owner: str, patch: SessionPatch) -> SessionSummary:
        self._owner(owner)
        return await self._store.update(session_id, owner, patch.model_copy(deep=True))

    def turn(
        self, session_id: str, owner: str, *, turn_id: str
    ) -> AbstractAsyncContextManager[SessionTurnLog]:
        self._owner(owner)
        return self._store.turn(session_id, owner, turn_id=turn_id)

    async def restore(self, session: SessionArchive, *, created_at: datetime) -> SessionSummary:
        self._owner(session.state.owner)
        return await self._store.restore(session.model_copy(deep=True), created_at=created_at)

    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]:
        self._owner(self._actor.id)
        raise Unsupported("maintenance_authority_required")
