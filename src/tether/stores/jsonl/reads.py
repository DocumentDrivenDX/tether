"""Owner-scoped projection reads shared by volatile and local durable stores."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Literal

from tether.stores.projections import SessionIndex
from tether.types.canonical import canonical
from tether.types.execution import Clock, Entry
from tether.types.storage import (
    ChangeGap,
    EffectInfo,
    EffectPage,
    EntryPage,
    JournalFiles,
    Page,
    PersistedChange,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
    StoreError,
    UnknownSession,
)

from .queries import page


class SessionReads:
    def __init__(self, files: JournalFiles, *, clock: Clock) -> None:
        self._loop = asyncio.get_running_loop()
        self.files, self.clock = files, clock
        self.index: dict[str, SessionIndex] = {}
        self.refs: dict[tuple[str, str], str] = {}
        self.sequences: dict[str, int] = {}
        self.feed: dict[str, list[PersistedChange]] = {}
        self.lock = asyncio.Lock()
        self._failed = False

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("wrong_event_loop")

    def owned(self, session_id: str, owner: str) -> SessionIndex:
        self._check_loop()
        if self._failed:
            raise StoreError("store_requires_reopen")
        data = self.index.get(session_id)
        if data is None or data.state.owner != owner:
            raise UnknownSession("unknown_session")
        return data

    async def get(self, session_id: str, owner: str) -> SessionView | None:
        self._check_loop()
        async with self.lock:
            if self._failed:
                raise StoreError("store_requires_reopen")
            data = self.index.get(session_id)
            return data.view().model_copy(deep=True) if data and data.state.owner == owner else None

    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot:
        self._check_loop()
        async with self.lock:
            data = self.owned(session_id, owner)
            return SessionSnapshot(
                view=data.view(), through_index=len(data.entries) - 1, seq=self.sequences[owner]
            ).model_copy(deep=True)

    async def entries(
        self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool = False
    ) -> EntryPage:
        self._check_loop()
        if start < 0 or not 0 < limit <= self.files.limits.page_limit:
            raise ValueError("invalid_page")
        async with self.lock:
            entries = self.owned(session_id, owner).entries
            indexes = (
                range(min(start, len(entries) - 1), -1, -1)
                if reverse
                else range(start, len(entries))
            )
            positions = list(indexes[: limit + 1])
            values = tuple(entries[i].model_copy(deep=True) for i in positions[:limit])
            return EntryPage(
                items=values, next_start=positions[-1] if len(positions) > limit else None
            )

    def _limit(self, limit: int) -> None:
        self._check_loop()
        if not 0 < limit <= self.files.limits.page_limit:
            raise ValueError("invalid_page")
        if self._failed:
            raise StoreError("store_requires_reopen")

    async def list(
        self, query: SessionQuery, *, cursor: str | None = None, limit: int = 50
    ) -> Page[SessionSummary]:
        self._limit(limit)
        async with self.lock:
            return page((item.summary for item in self.index.values()), query, cursor, limit)

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
        self._limit(limit)
        async with self.lock:
            data = self.owned(session_id, owner)
            if kind == "key":
                entry = data.keys.get(value)
                entries = (entry,) if entry is not None else ()
            else:
                entries = data.lookups.get((kind, value), ())
            if len(entries) > limit:
                raise StoreError("lookup_limit")
            maximum = max_bytes if max_bytes is not None else self.files.limits.max_batch_bytes
            if maximum <= 0:
                raise ValueError("invalid_read_bytes")
            used = 0
            for entry in entries:
                used += len(canonical(entry.model_dump(mode="json")))
                if used > maximum:
                    raise StoreError("lookup_bytes")
            return tuple(e.model_copy(deep=True) for e in entries)

    async def find(self, owner: str, ref: str) -> SessionSummary | None:
        self._limit(1)
        async with self.lock:
            session_id = self.refs.get((owner, ref))
            return self.index[session_id].summary.model_copy(deep=True) if session_id else None

    async def effects(
        self, session_id: str, owner: str, *, start: int = 0, limit: int = 50
    ) -> EffectPage:
        self._limit(limit)
        if start < 0:
            raise ValueError("invalid_page")
        async with self.lock:
            effects = self.owned(session_id, owner).effects
            entries = sorted(
                (e for e in effects.values() if e.index >= start), key=lambda e: e.index
            )
            return EffectPage(
                items=tuple(
                    EffectInfo(
                        invocation_id=str(e.facts["invocation_id"]),
                        tool=str(e.facts["tool"]),
                        intent_index=e.index,
                        reason=str(e.facts.get("reason") or "outcome_unknown"),
                    )
                    for e in entries[:limit]
                ),
                next_start=entries[limit].index if len(entries) > limit else None,
            )

    async def changes(
        self, owner: str, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncGenerator[PersistedChange, None]:
        self._limit(1)
        async with self.lock:
            if session_id is not None:
                self.owned(session_id, owner)
            position = self.sequences.get(owner, 0) if since is None else since
            if position < 0 or position > self.sequences.get(owner, 0):
                raise ValueError("invalid_sequence")
        while True:
            async with self.lock:
                self._limit(1)
                retained = self.feed.get(owner, [])
                if retained and position < retained[0].seq - 1:
                    raise ChangeGap("too_old")
                batch = tuple(c for c in retained if c.seq > position)[
                    : self.files.limits.page_limit
                ]
            for change in batch:
                position = change.seq
                if session_id is None or change.session_id == session_id:
                    yield change.model_copy(deep=True)
            if not batch:
                await asyncio.sleep(self.files.limits.change_poll_seconds)
