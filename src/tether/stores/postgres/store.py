"""Caller-bound SQL session-store composition; acquisition and maintenance are explicit."""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from uuid import uuid4

from tether.stores.projections import patch_entries
from tether.types.execution import Actor, copy_actor
from tether.types.mutations import CreateReceipt, UpdateReceipt, updated
from tether.types.storage import (
    RecoveredTurn,
    SessionArchive,
    SessionInit,
    SessionPatch,
    SessionSummary,
    Unsupported,
)

from .capture import Capture
from .handle import TurnHandle
from .recovery import Recovery
from .store_reads import StoreReads
from .turns import lease


class PostgresSessionStore(StoreReads):
    def for_actor(self, actor: Actor) -> "PostgresSessionStore":
        self._check()
        if self._actor is not None:
            self._owner(actor.id)
        return PostgresSessionStore(self.root, actor=copy_actor(actor))

    async def aclose(self) -> bool:
        self.root.check_loop()
        self._closed = True
        return True if self._actor is not None else await self.root.aclose()

    async def create(self, owner: str, init: SessionInit) -> SessionSummary:
        return (await self.create_with_receipt(owner, init)).summary

    async def create_with_receipt(self, owner: str, init: SessionInit) -> CreateReceipt:
        store = Capture(self.root, self._owner(owner))
        return await store.create_with_receipt(init.model_copy(deep=True), str(uuid4()))

    def turn(
        self, session_id: str, owner: str, *, turn_id: str
    ) -> AbstractAsyncContextManager[TurnHandle]:
        self._owner(owner)
        return self._turn(session_id, owner, turn_id)

    @asynccontextmanager
    async def _turn(
        self, session_id: str, owner: str, turn_id: str
    ) -> AsyncGenerator[TurnHandle, None]:
        store = Capture(self.root, self._owner(owner))
        async with lease(store, session_id, turn_id) as token:
            index, prefix = await store.load(token)
            handle = TurnHandle(store, token, index, prefix)
            try:
                yield handle
            finally:
                handle.seal()

    async def update(self, session_id: str, owner: str, patch: SessionPatch) -> SessionSummary:
        return (await self.update_with_receipt(session_id, owner, patch)).summary

    async def update_with_receipt(
        self, session_id: str, owner: str, patch: SessionPatch
    ) -> UpdateReceipt:
        store = Capture(self.root, self._owner(owner))
        values = patch_entries(patch.model_copy(deep=True))
        async with lease(store, session_id, "update:" + str(uuid4())) as token:
            result = await store.commit(token, values)
            return updated(result.summary, result.entries)

    async def restore(self, session: SessionArchive, *, created_at: datetime) -> SessionSummary:
        store = Capture(self.root, self._owner(session.state.owner))
        return await store.restore(session.model_copy(deep=True), created_at=created_at)

    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]:
        self._check()
        if self._actor is not None or self._maintenance is None:
            raise Unsupported("maintenance_authority_required")
        return await Recovery(self.root, copy_actor(self._maintenance)).recover(now=now)

    async def recover_owner(self, *, now: datetime) -> tuple[RecoveredTurn, ...]:
        actor = self._caller()
        return await Recovery(self.root, actor, owner=actor.id).recover(now=now)
