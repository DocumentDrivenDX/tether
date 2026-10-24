"""Bounded volatile authoritative archive; no durability or eviction claim."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tether.stores.jsonl.queries import page
from tether.stores.projections import create_index
from tether.types.archive import (
    ArchiveCapabilities,
    ArchivedSession,
    ArchiveLimits,
    LegacyCallDetail,
)
from tether.types.archive_codec import references, verify_bundle
from tether.types.canonical import canonical
from tether.types.execution import Actor, EntryConflict, HistoryRef
from tether.types.history import HistoryValue, StoredHistory
from tether.types.storage import (
    EntryPage,
    IntegrityError,
    Page,
    SessionQuery,
    SessionSummary,
    SessionView,
    StoreError,
    UnknownSession,
    Unsupported,
)


@dataclass(frozen=True)
class Item:
    session: ArchivedSession
    records: dict[bytes, StoredHistory]
    size: int


def ref_key(ref: HistoryRef) -> bytes:
    return canonical(ref.model_dump(mode="json"))


def checked(session: ArchivedSession) -> None:
    verify_bundle(session.bundle)
    bundle, view = session.bundle, session.view
    index = create_index(
        bundle.session.state.id, bundle.session.state.owner, bundle.session.init, bundle.created_at
    )
    for entry in bundle.session.entries:
        index.apply(entry)
    summary = index.summary.model_copy(
        update={"tier": "archive", "last_seq": view.summary.last_seq}
    )
    projected = index.view().model_copy(update={"summary": summary})
    if index.state != bundle.session.state or projected != view:
        raise IntegrityError("archive_projection_integrity")
    if summary.status == "running":
        raise StoreError("archive_session_running")


class InMemoryArchive:
    def __init__(self, limits: ArchiveLimits) -> None:
        self.limits = limits.model_copy(deep=True)
        self.capabilities = ArchiveCapabilities(
            kind="authoritative", writable=True, lossless=True, restorable=True, content="full"
        )
        self._items: dict[str, Item] = {}
        self._bytes = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def _check(self, count: int = 1) -> None:
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        if self._loop is not current:
            raise RuntimeError("wrong_event_loop")
        if not 0 < count <= self.limits.max_entries:
            raise StoreError("archive_limit")

    def _owned(self, sid: str, actor: Actor) -> Item | None:
        item = self._items.get(sid)
        return item if item is not None and item.session.view.summary.owner == actor.id else None

    async def archive(self, session: ArchivedSession) -> None:
        self._check(max(1, len(session.bundle.session.entries)))
        size = len(canonical(session.model_dump(mode="json")))
        if size > self.limits.max_bytes:
            raise StoreError("archive_limit")
        checked(session)
        sid = session.view.summary.id
        old = self._items.get(sid)
        if old is not None:
            if (
                old.session.bundle.session.state.owner,
                old.session.bundle.created_at,
                old.session.bundle.session.init.ref,
            ) != (
                session.bundle.session.state.owner,
                session.bundle.created_at,
                session.bundle.session.init.ref,
            ):
                raise EntryConflict("archive_snapshot_conflict")
            if old.session == session:
                return
            previous, incoming = old.session.bundle.session.entries, session.bundle.session.entries
            if len(incoming) <= len(previous) or incoming[: len(previous)] != previous:
                raise EntryConflict("archive_snapshot_conflict")
        if old is None and len(self._items) >= self.limits.max_sessions:
            raise StoreError("archive_limit")
        total = self._bytes + size - (old.size if old is not None else 0)
        if total > self.limits.max_bytes:
            raise StoreError("archive_limit")
        reachable = {
            ref_key(ref)
            for entry in session.bundle.session.entries
            for ref in references(entry.model_dump(mode="json"))
        }
        records = [StoredHistory.model_validate(raw) for raw in session.bundle.records]
        self._items[sid] = Item(
            session.model_copy(deep=True),
            {ref_key(record.ref): record for record in records if ref_key(record.ref) in reachable},
            size,
        )
        self._bytes = total

    async def list(
        self, query: SessionQuery, *, cursor: str | None, limit: int, actor: Actor
    ) -> Page[SessionSummary]:
        self._check(limit)
        if query.owner != actor.id:
            raise UnknownSession("unknown_session")
        return page(
            (item.session.view.summary for item in self._items.values()),
            query,
            cursor,
            limit,
            tier="archive",
        )

    async def get(self, session_id: str, *, actor: Actor) -> SessionView | None:
        self._check()
        item = self._owned(session_id, actor)
        return item.session.view.model_copy(deep=True) if item is not None else None

    async def entries(
        self, session_id: str, *, actor: Actor, start: int, limit: int, reverse: bool
    ) -> EntryPage:
        self._check(limit)
        item = self._owned(session_id, actor)
        if item is None:
            raise UnknownSession("unknown_session")
        if start < 0:
            raise ValueError("invalid_page")
        entries = item.session.bundle.session.entries
        indexes = (
            range(min(start, len(entries) - 1), -1, -1) if reverse else range(start, len(entries))
        )
        positions = list(indexes[: limit + 1])
        return EntryPage(
            items=tuple(entries[i].model_copy(deep=True) for i in positions[:limit]),
            next_start=positions[-1] if len(positions) > limit else None,
        )

    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]:
        self._check(max(1, len(refs)))
        values: list[HistoryValue] = []
        for ref in refs:
            item = self._owned(ref.session_id, actor)
            stored = item.records.get(ref_key(ref)) if item is not None else None
            if ref.owner != actor.id or stored is None:
                values.append(HistoryValue(ref=ref, status="denied", reason="history_scope"))
            elif ref.codec != "tether-history-v1" or ref.component != "record":
                values.append(
                    HistoryValue(ref=ref, status="unsupported", reason="unsupported_history_codec")
                )
            else:
                values.append(
                    HistoryValue(
                        ref=ref, status="ready", record=stored.record.model_copy(deep=True)
                    )
                )
        return tuple(values)

    async def restore(self, session_id: str, *, actor: Actor) -> ArchivedSession:
        self._check()
        item = self._owned(session_id, actor)
        if item is None:
            raise UnknownSession("unknown_session")
        return item.session.model_copy(deep=True)

    async def call_detail(
        self, request_ids: Sequence[str], *, actor: Actor
    ) -> Mapping[str, LegacyCallDetail]:
        self._check(max(1, len(request_ids)))
        raise Unsupported("legacy_call_detail_unsupported")
