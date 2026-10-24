"""Atomic JSONL journal batches, materialized projections and process-scoped leases."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

from tether.stores.projections import SessionIndex, create_index
from tether.types.canonical import canonical
from tether.types.execution import Clock, CommitUnknown, Entry, NewEntry, SessionState
from tether.types.storage import (
    EntryPage,
    IntegrityError,
    JournalEvent,
    JournalFiles,
    PersistedChange,
    RecoveredTurn,
    SessionArchive,
    SessionClosed,
    SessionInit,
    SessionSnapshot,
    SessionSummary,
    SessionView,
    StoreCapabilities,
    StoreError,
    UnknownSession,
)

from .leases import Lease, Leases


def utc_now() -> datetime:
    return datetime.now(UTC)


class JsonlSessionStore:
    def __init__(self, files: JournalFiles, *, clock: Clock = utc_now) -> None:
        self.files, self.clock = files, clock
        self.capabilities = StoreCapabilities(
            durability=files.durability, lease="process", fencing=True
        )
        self.index: dict[str, SessionIndex] = {}
        self.refs: dict[tuple[str, str], str] = {}
        self.sequences: dict[str, int] = {}
        self.feed: dict[str, list[PersistedChange]] = {}
        self.leases = Leases(clock, files.limits.lease_seconds)
        self.lock = asyncio.Lock()
        self._failed = False
        self._index_bytes = 0

    @classmethod
    async def open(cls, files: JournalFiles, *, clock: Clock = utc_now) -> Self:
        files.claim("journal")
        store = cls(files, clock=clock)
        async for item in files.scan("journal"):
            store._publish(JournalEvent.model_validate(item.value))
        return store

    def owned(self, session_id: str, owner: str) -> SessionIndex:
        if self._failed:
            raise StoreError("store_requires_reopen")
        data = self.index.get(session_id)
        if data is None or data.state.owner != owner:
            raise UnknownSession("unknown_session")
        return data

    async def _commit(self, event: JournalEvent, guard: Callable[[], None] | None = None) -> None:
        if self._failed:
            raise StoreError("store_requires_reopen")
        self._check_capacity(event)
        try:
            await self.files.append_file("journal", event.model_dump(mode="json"), guard=guard)
        except (asyncio.CancelledError, CommitUnknown):
            self._failed = True
            raise
        except OSError:
            self._failed = True
            raise CommitUnknown("journal_commit_unknown") from None
        try:
            self._publish(event)
        except Exception:
            self._failed = True
            raise CommitUnknown("projection_publish_unknown") from None

    def _check_capacity(self, event: JournalEvent) -> int:
        size = sum(len(canonical(e.model_dump(mode="json"))) for e in event.entries)
        if event.init is not None:
            size += len(canonical(event.init.model_dump(mode="json"))) + 1024
        if self._index_bytes + size > self.files.limits.max_index_bytes:
            raise StoreError("journal_index_limit")
        return size

    def _publish(self, event: JournalEvent) -> None:
        size = self._check_capacity(event)
        if event.operation in {"lease", "heartbeat", "release"}:
            self._lease_event(event)
            return
        if event.operation in {"create", "restore"}:
            if event.init is None or event.session_id in self.index:
                raise IntegrityError("session_create_invalid")
            staged = create_index(event.session_id, event.owner, event.init, event.at)
        else:
            staged = self.owned(event.session_id, event.owner).clone()
        for entry in event.entries:
            staged.apply(entry)
        change = event.change
        if change is None or change.seq != self.sequences.get(event.owner, 0) + 1:
            raise IntegrityError("owner_sequence_invalid")
        staged.summary = staged.summary.model_copy(update={"last_seq": change.seq})
        expected = PersistedChange(
            seq=change.seq,
            session_id=event.session_id,
            kind="updated",
            summary=staged.summary,
            entries=event.entries,
        )
        if staged.summary != event.summary or expected != change:
            raise IntegrityError("projection_integrity")
        self._index_bytes += size
        self.index[event.session_id] = staged
        if staged.init.ref is not None:
            self.refs[(event.owner, staged.init.ref)] = event.session_id
        self.sequences[event.owner] = change.seq
        changes = self.feed.setdefault(event.owner, [])
        changes.append(change)
        del changes[: -self.files.limits.feed_retention]

    def _lease_event(self, event: JournalEvent) -> None:
        self.owned(event.session_id, event.owner)
        previous = self.leases.generations.get(event.session_id, 0)
        if event.generation < previous or event.generation > previous + 1:
            raise IntegrityError("lease_generation_invalid")
        lease = None
        if event.operation != "release":
            if event.expires_at is None or event.turn_id is None:
                raise IntegrityError("lease_invalid")
            lease = Lease(event.generation, event.expires_at, event.turn_id)
        self.leases.publish(event.session_id, lease, event.generation)

    def _event(
        self,
        operation: str,
        staged: SessionIndex,
        entries: tuple[Entry, ...],
        init: SessionInit | None = None,
    ) -> JournalEvent:
        sequence = self.sequences.get(staged.state.owner, 0) + 1
        summary = staged.summary.model_copy(update={"last_seq": sequence})
        change = PersistedChange(
            seq=sequence,
            session_id=staged.state.id,
            kind="updated",
            summary=summary,
            entries=entries,
        )
        return JournalEvent.model_validate(
            {
                "operation": operation,
                "session_id": staged.state.id,
                "owner": staged.state.owner,
                "at": staged.summary.created_at if init is not None else self.clock(),
                "init": init,
                "entries": entries,
                "summary": summary,
                "change": change,
            }
        )

    async def create(self, owner: str, init: SessionInit) -> SessionSummary:
        async with self.lock:
            existing = self.refs.get((owner, init.ref)) if init.ref is not None else None
            if existing is not None:
                return self.index[existing].summary.model_copy(deep=True)
            staged = create_index(str(uuid4()), owner, init.model_copy(deep=True), self.clock())
            await self._commit(self._event("create", staged, (), staged.init))
            return self.index[staged.state.id].summary.model_copy(deep=True)

    async def restore(self, session: SessionArchive, *, created_at: datetime) -> SessionSummary:
        from tether.types.execution import EntryConflict

        async with self.lock:
            existing = self.index.get(session.state.id)
            if existing is not None:
                if existing.entries != list(session.entries) or existing.state != session.state:
                    raise EntryConflict("archive_restore_conflict")
                return existing.summary.model_copy(deep=True)
            ref = session.init.ref
            if ref is not None and (session.state.owner, ref) in self.refs:
                raise EntryConflict("archive_restore_conflict")
            staged = create_index(session.state.id, session.state.owner, session.init, created_at)
            for entry in session.entries:
                staged.apply(entry)
            if staged.state != session.state:
                raise IntegrityError("archive_state_integrity")
            await self._commit(self._event("restore", staged, session.entries, session.init))
            return self.index[session.state.id].summary.model_copy(deep=True)

    async def get(self, session_id: str, owner: str) -> SessionView | None:
        async with self.lock:
            if self._failed:
                raise StoreError("store_requires_reopen")
            data = self.index.get(session_id)
            return data.view().model_copy(deep=True) if data and data.state.owner == owner else None

    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot:
        async with self.lock:
            data = self.owned(session_id, owner)
            return SessionSnapshot(
                view=data.view(), through_index=len(data.entries) - 1, seq=self.sequences[owner]
            ).model_copy(deep=True)

    async def entries(
        self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool = False
    ) -> EntryPage:
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

    async def append(
        self, session_id: str, owner: str, generation: int, entries: Sequence[NewEntry]
    ) -> tuple[Entry, ...]:
        async with self.lock:
            self.leases.check(session_id, generation)
            staged = self.owned(session_id, owner).clone()
            before = len(staged.entries)
            accepted = staged.append(tuple(entries), self.clock())
            new = tuple(staged.entries[before:])
            if new:

                def guard() -> None:
                    self.leases.check(session_id, generation)

                await self._commit(self._event("append", staged, new), guard)
            return tuple(e.model_copy(deep=True) for e in accepted)

    @asynccontextmanager
    async def turn(
        self, session_id: str, owner: str, *, turn_id: str
    ) -> AsyncGenerator[TurnHandle, None]:
        async with self.lock:
            data = self.owned(session_id, owner)
            if data.summary.status == "closed":
                raise SessionClosed("session_closed")
            await self._recover_one(session_id, self.clock())
            lease = self.leases.next(session_id, turn_id)
            await self._commit(
                JournalEvent(
                    operation="lease",
                    session_id=session_id,
                    owner=owner,
                    at=self.clock(),
                    generation=lease.generation,
                    expires_at=lease.expires_at,
                    turn_id=turn_id,
                )
            )
            handle = TurnHandle(self, session_id, owner, lease.generation, turn_id)
        try:
            yield handle
        finally:
            await asyncio.shield(self.release(session_id, owner, lease.generation))

    async def heartbeat(self, session_id: str, owner: str, generation: int) -> None:
        async with self.lock:
            self.leases.check(session_id, generation)
            lease = self.leases.active[session_id]
            await self._commit(
                JournalEvent(
                    operation="heartbeat",
                    session_id=session_id,
                    owner=owner,
                    at=self.clock(),
                    generation=generation,
                    turn_id=lease.turn_id,
                    expires_at=self.clock() + timedelta(seconds=self.files.limits.lease_seconds),
                ),
                lambda: self.leases.check(session_id, generation),
            )

    async def release(self, session_id: str, owner: str, generation: int) -> None:
        if self._failed:
            return
        async with self.lock:
            lease = self.leases.active.get(session_id)
            if lease and lease.generation == generation:
                data = self.owned(session_id, owner)
                if data.summary.active_turn_id is not None:
                    staged = data.clone()
                    values = staged.append(
                        (
                            NewEntry(
                                kind="turn_ended",
                                turn_id=data.summary.active_turn_id,
                                facts={"stop_reason": "interrupted"},
                            ),
                        ),
                        self.clock(),
                    )
                    await self._commit(self._event("append", staged, values))
                await self._commit(
                    JournalEvent(
                        operation="release",
                        session_id=session_id,
                        owner=owner,
                        at=self.clock(),
                        generation=generation,
                        turn_id=lease.turn_id,
                    )
                )

    async def _recover_one(self, session_id: str, now: datetime) -> RecoveredTurn | None:
        lease = self.leases.active.get(session_id)
        if lease is None or lease.expires_at > now:
            return None
        data = self.index[session_id]
        recovered = None
        if data.summary.active_turn_id:
            staged = data.clone()
            staged.append(
                (
                    NewEntry(
                        kind="turn_ended",
                        turn_id=data.summary.active_turn_id,
                        facts={"stop_reason": "interrupted"},
                    ),
                ),
                now,
            )
            await self._commit(self._event("append", staged, (staged.entries[-1],)))
            recovered = RecoveredTurn(
                session_id=session_id, turn_id=data.summary.active_turn_id, resumable=True
            )
        await self._commit(
            JournalEvent(
                operation="release",
                session_id=session_id,
                owner=data.state.owner,
                at=now,
                generation=lease.generation,
                turn_id=lease.turn_id,
            )
        )
        return recovered

    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]:
        async with self.lock:
            result = [
                await self._recover_one(session_id, now) for session_id in tuple(self.leases.active)
            ]
            return tuple(item for item in result if item is not None)


class TurnHandle:
    def __init__(
        self, store: JsonlSessionStore, session_id: str, owner: str, generation: int, turn_id: str
    ) -> None:
        self.store, self.session_id, self.owner = store, session_id, owner
        self.generation, self.turn_id = generation, turn_id

    @property
    def session(self) -> SessionState:
        return self.store.owned(self.session_id, self.owner).state.model_copy(deep=True)

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(
            e.model_copy(deep=True) for e in self.store.owned(self.session_id, self.owner).entries
        )

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        return await self.store.append(self.session_id, self.owner, self.generation, entries)

    async def heartbeat(self) -> None:
        await self.store.heartbeat(self.session_id, self.owner, self.generation)
