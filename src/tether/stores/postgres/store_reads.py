"""Owner-checked public read ports over bounded indexed SQL operations."""

from typing import Literal

from tether.types.execution import Entry
from tether.types.storage import (
    ChangeFeed,
    EffectPage,
    EntryPage,
    Page,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
)

from .scope import Scope


class StoreReads(Scope):
    async def get(self, session_id: str, owner: str) -> SessionView | None:
        return await self._reads(owner).get(session_id)

    async def list(
        self, query: SessionQuery, *, cursor: str | None = None, limit: int = 50
    ) -> Page[SessionSummary]:
        return await self._reads(query.owner).list(query, cursor=cursor, limit=limit)

    async def find(self, owner: str, ref: str) -> SessionSummary | None:
        return await self._reads(owner).find(ref)

    async def effects(
        self, session_id: str, owner: str, *, start: int = 0, limit: int = 50
    ) -> EffectPage:
        return await self._reads(owner).effects(session_id, start=start, limit=limit)

    async def watermark(self, owner: str) -> int:
        return await self._reads(owner).watermark()

    async def lookup(
        self,
        session_id: str,
        owner: str,
        *,
        kind: Literal["key", "invocation", "model_call", "turn", "history_link"],
        value: str,
        limit: int = 1000,
        max_bytes: int | None = None,
    ) -> tuple[Entry, ...]:
        return await self._reads(owner).lookup(
            session_id, kind=kind, value=value, limit=limit, max_bytes=max_bytes
        )

    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot:
        return await self._reads(owner).snapshot(session_id)

    async def entries(
        self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool = False
    ) -> EntryPage:
        return await self._reads(owner).entries(
            session_id, start=start, limit=limit, reverse=reverse
        )

    def changes(
        self, owner: str, *, session_id: str | None = None, since: int | None = None
    ) -> ChangeFeed:
        self._owner(owner)

        def guard() -> None:
            self._owner(owner)

        return self._reads(owner).changes(session_id=session_id, since=since, guard=guard)
