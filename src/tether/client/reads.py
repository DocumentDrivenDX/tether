"""The owner-bound UI read facade; no engine/store imports are needed by a UI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from tether.sessions import content, disclosure
from tether.sessions.access import SessionAccess
from tether.sessions.archived import ArchiveAccess
from tether.types.canonical import canonical
from tether.types.client import CallDetail, HistoryPage, ReadLimit
from tether.types.execution import Actor, ExecutionDenied, copy_actor
from tether.types.storage import (
    EffectPage,
    EntryPage,
    Page,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
    UnknownSession,
    Unsupported,
)


class ClientReads:
    def __init__(
        self, access: SessionAccess, actor: Actor, archives: ArchiveAccess | None = None
    ) -> None:
        self._access, self._actor = access, copy_actor(actor)
        self._archives = archives or ArchiveAccess(access, None, None)

    @staticmethod
    def _tier(tier: str | None) -> None:
        if tier not in {None, "live"}:
            raise Unsupported("archive_unconfigured")

    async def list(
        self,
        query: SessionQuery | None = None,
        *,
        tier: Literal["live", "archive"] = "live",
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[SessionSummary]:
        query = query or SessionQuery(owner=self._actor.id)
        if tier == "archive":
            return await self._archives.list(self._actor, query, cursor=cursor, limit=limit)
        self._tier(tier)
        self._access.limit(limit)
        if query.owner != self._actor.id:
            raise ExecutionDenied("denied")

        async def operation() -> Page[SessionSummary]:
            page = await self._access.store_for(self._actor).list(query, cursor=cursor, limit=limit)
            for item in page.items:
                await self._access.authorize(
                    self._actor,
                    item.id,
                    session_id=item.id,
                )
            return page

        return await self._access.metadata(self._actor, "owner:" + self._actor.id, operation)

    async def get(
        self, session_id: str, *, tier: Literal["live", "archive"] | None = None
    ) -> SessionView:
        if tier == "archive":
            return await self._archives.get(self._actor, session_id)
        self._tier(tier)

        async def operation() -> SessionView:
            view = await self._access.store_for(self._actor).get(session_id, self._actor.id)
            if view is None:
                raise UnknownSession("unknown_session")
            return view

        try:
            return await self._access.metadata(
                self._actor,
                session_id,
                operation,
                session_id=session_id,
            )
        except UnknownSession:
            if tier is not None or self._archives.source is None:
                raise
            return await self._archives.get(self._actor, session_id)

    async def entries(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] | None = None,
        start: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> EntryPage:
        if tier == "archive":
            return await self._archives.entries(self._actor, session_id, start, limit, reverse)
        self._tier(tier)
        self._access.limit(limit)
        self._access.position(start)
        try:
            return await self._access.metadata(
                self._actor,
                session_id,
                lambda: self._access.store_for(self._actor).entries(
                    session_id, self._actor.id, start=start, limit=limit, reverse=reverse
                ),
                session_id=session_id,
            )
        except UnknownSession:
            if tier is not None or self._archives.source is None:
                raise
            return await self._archives.entries(self._actor, session_id, start, limit, reverse)

    async def snapshot(self, session_id: str) -> SessionSnapshot:
        return await self._access.metadata(
            self._actor,
            session_id,
            lambda: self._access.store_for(self._actor).snapshot(session_id, self._actor.id),
            session_id=session_id,
        )

    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage:
        self._access.limit(limit)
        self._access.position(start)
        return await self._access.metadata(
            self._actor,
            session_id,
            lambda: self._access.store_for(self._actor).effects(
                session_id, self._actor.id, start=start, limit=limit
            ),
            session_id=session_id,
        )

    async def find(self, ref: str) -> SessionSummary | None:
        async def operation() -> SessionSummary | None:
            value = await self._access.store_for(self._actor).find(self._actor.id, ref)
            if value is not None:
                await self._access.authorize(
                    self._actor,
                    value.id,
                    session_id=value.id,
                )
            return value

        return await self._access.metadata(self._actor, "owner:" + self._actor.id, operation)

    async def history(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] = "live",
        start: int = 0,
        limit: int = 100,
    ) -> HistoryPage:
        if tier == "archive":
            return await self._archives.history(self._actor, session_id, start, limit)
        self._tier(tier)
        self._access.limit(limit)
        self._access.position(start)
        return await self._access.metadata(
            self._actor,
            session_id,
            lambda: content.history_page(self._access, self._actor, session_id, start, limit),
            disclose=lambda page: disclosure.history(self._access, self._actor, session_id, page),
            session_id=session_id,
        )

    @staticmethod
    def _ids(values: Sequence[object]) -> tuple[str, ...]:
        result: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ReadLimit("read_limit")
            result.append(value)
        return tuple(result)

    async def call_detail(
        self, session_id: str, model_call_ids: Sequence[str]
    ) -> tuple[CallDetail, ...]:
        if isinstance(model_call_ids, (str, bytes)):
            raise ReadLimit("read_limit")
        self._access.limit(len(model_call_ids), maximum=self._access.limits.max_call_ids)
        ids = self._ids(model_call_ids)
        if len(canonical(list(ids))) > self._access.limits.max_request_bytes:
            raise ReadLimit("read_bytes")
        page = await self._access.metadata(
            self._actor,
            session_id,
            lambda: content.call_details(self._access, self._actor, session_id, ids),
            disclose=lambda page: disclosure.calls(self._access, self._actor, session_id, page),
            session_id=session_id,
        )
        return page.items
