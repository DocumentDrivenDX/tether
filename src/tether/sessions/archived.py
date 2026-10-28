"""Optional caller-authorized archive reads and explicit transfer composition."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from tether.types import Payload
from tether.types.archive import (
    ArchiveCapabilities,
    ArchivedSession,
    ArchiveLimits,
    ArchiveReadError,
    ArchiveSink,
    ArchiveSource,
    NotRestorable,
)
from tether.types.archive_codec import references, verify_bundle
from tether.types.archive_cursor import unwrap, wrap
from tether.types.client import HistoryPage, HistorySlot, ReadLimit
from tether.types.execution import (
    Actor,
    Entry,
    ExecutionDenied,
    HistoryRef,
    WorkerOutstanding,
    copy_actor,
)
from tether.types.history import HistoryValue
from tether.types.history_links import select as select_history_ref
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

from . import disclosure
from .access import SessionAccess
from .archive import ArchiveTransfer

T = TypeVar("T", bound=Payload | None)


class ArchiveAccess:
    def __init__(
        self, access: SessionAccess, source: ArchiveSource | None, sink: ArchiveSink | None
    ) -> None:
        self.access, self.source, self.sink = access, source, sink
        self.capabilities = (
            ArchiveCapabilities.model_validate(source.capabilities) if source is not None else None
        )

    def selected(self) -> ArchiveSource:
        self.access.check()
        if self.source is None:
            raise Unsupported("archive_unconfigured")
        return self.source

    async def bounded(
        self,
        actor: Actor,
        sid: str,
        operation: Callable[[], Awaitable[T]],
        *,
        disclose: Callable[[T], Awaitable[T]] | None = None,
        session_id: str | None = None,
    ) -> T:
        try:
            return await self.access.metadata(
                actor,
                sid,
                operation,
                disclose=disclose,
                session_id=session_id,
            )
        except TimeoutError:
            raise ArchiveReadError("unavailable") from None

    def summary(self, actor: Actor, summary: SessionSummary) -> SessionSummary:
        if summary.owner != actor.id or summary.tier != "archive":
            raise IntegrityError("archive_scope")
        if self.capabilities is not None and self.capabilities.kind == "derived":
            return summary.model_copy(update={"partial": True})
        return summary

    async def list(
        self, actor: Actor, query: SessionQuery, *, cursor: str | None, limit: int
    ) -> Page[SessionSummary]:
        query = query.model_copy(deep=True)
        source = self.selected()
        self.access.limit(limit)
        if query.owner != actor.id:
            raise ExecutionDenied("denied")
        if cursor is not None and len(cursor.encode()) > self.access.limits.max_request_bytes:
            raise ReadLimit("read_bytes")
        after = unwrap(cursor, query)

        async def operation() -> Page[SessionSummary]:
            page = await source.list(
                query.model_copy(deep=True), cursor=after, limit=limit, actor=copy_actor(actor)
            )
            if len(page.items) > limit:
                raise ReadLimit("read_limit")
            items = tuple(self.summary(actor, item) for item in page.items)
            return page.model_copy(update={"items": items, "next": wrap(page.next, query)})

        async def disclose(page: Page[SessionSummary]) -> Page[SessionSummary]:
            for item in page.items:
                await self.access.authorize(
                    actor,
                    item.id,
                    session_id=item.id,
                )
            return page

        return await self.bounded(actor, "owner:" + actor.id, operation, disclose=disclose)

    async def view(self, actor: Actor, sid: str) -> SessionView:
        value = await self.selected().get(sid, actor=copy_actor(actor))
        if value is None:
            raise UnknownSession("unknown_session")
        if value.summary.id != sid:
            raise IntegrityError("archive_scope")
        return value.model_copy(update={"summary": self.summary(actor, value.summary)}, deep=True)

    async def get(self, actor: Actor, sid: str) -> SessionView:
        return await self.bounded(actor, sid, lambda: self.view(actor, sid), session_id=sid)

    async def page(
        self, actor: Actor, sid: str, start: int, limit: int, reverse: bool
    ) -> EntryPage:
        await self.view(actor, sid)
        page = await self.selected().entries(
            sid, actor=copy_actor(actor), start=start, limit=limit, reverse=reverse
        )
        if len(page.items) > limit:
            raise ReadLimit("read_limit")
        self.access.size(page)
        for entry in page.items:
            for ref in references(entry.model_dump(mode="json")):
                if (ref.owner, ref.session_id) != (actor.id, sid):
                    raise IntegrityError("archive_scope")
        partial = page.partial or (
            self.capabilities is not None and self.capabilities.kind == "derived"
        )
        return page.model_copy(update={"partial": partial}, deep=True)

    async def entries(
        self, actor: Actor, sid: str, start: int, limit: int, reverse: bool
    ) -> EntryPage:
        self.access.limit(limit)
        self.access.position(start)
        return await self.bounded(
            actor, sid, lambda: self.page(actor, sid, start, limit, reverse), session_id=sid
        )

    async def record(self, actor: Actor, sid: str, ref: HistoryRef) -> HistoryValue:
        await self.access.authorize(
            actor,
            sid,
            session_id=sid,
        )
        if ref.kind == "model_exchange" and not await self.access.allowed(
            actor,
            sid,
            "read_call_payload",
            session_id=sid,
        ):
            return HistoryValue(ref=ref, status="denied", reason="policy_denied")
        if self.capabilities is not None and self.capabilities.content == "facts_only":
            return HistoryValue(ref=ref, status="unsupported", reason="archive_facts_only")
        values = await self.selected().read((ref,), actor=copy_actor(actor))
        value = self.access.verify(ref, values)
        await self.access.authorize(
            actor,
            sid,
            session_id=sid,
        )
        if ref.kind == "model_exchange" and not await self.access.allowed(
            actor,
            sid,
            "read_call_payload",
            session_id=sid,
        ):
            return HistoryValue(ref=ref, status="denied", reason="policy_denied")
        return value

    async def _link_entries(self, actor: Actor, sid: str, through_index: int) -> tuple[Entry, ...]:
        if through_index + 1 > self.access.limits.max_lookup_entries:
            raise ReadLimit("read_limit")
        page = await self.selected().entries(
            sid,
            actor=copy_actor(actor),
            start=0,
            limit=self.access.limits.max_lookup_entries,
            reverse=False,
        )
        if (
            page.partial
            or len(page.items) < through_index + 1
            or any(e.index != pos for pos, e in enumerate(page.items[: through_index + 1]))
        ):
            raise ArchiveReadError("unavailable")
        return tuple(e for e in page.items if e.kind == "history_linked")

    async def _linked_record(
        self,
        actor: Actor,
        sid: str,
        entry: Entry,
        ref: HistoryRef,
        links: tuple[Entry, ...],
        through_index: int,
    ) -> HistoryValue:
        try:
            active = select_history_ref(
                ref,
                entry.index,
                tuple(e for e in links if e.facts.get("message_index") == entry.index),
                through_index=through_index,
                max_hops=self.access.limits.max_history_link_hops,
            )
        except IntegrityError:
            return HistoryValue(ref=ref, status="integrity_error", reason="history_link_integrity")
        return await self.record(actor, sid, active)

    async def history(self, actor: Actor, sid: str, start: int, limit: int) -> HistoryPage:
        self.access.limit(limit)
        self.access.position(start)

        async def operation() -> HistoryPage:
            page = await self.page(actor, sid, start, limit, False)
            view = await self.view(actor, sid)
            through_index = view.summary.entry_count - 1
            links = await self._link_entries(actor, sid, through_index)
            slots: list[HistorySlot] = []
            used = 0
            count = 0
            for entry in page.items:
                if entry.kind == "history_linked":
                    continue
                for ref in references(entry.model_dump(mode="json")):
                    count += 1
                    if count > self.access.limits.max_lookup_entries:
                        raise ReadLimit("read_limit")
                    value = await self._linked_record(actor, sid, entry, ref, links, through_index)
                    slot = HistorySlot(entry_index=entry.index, value=value)
                    used += self.access.size(slot)
                    if used > self.access.limits.max_read_bytes:
                        raise ReadLimit("read_bytes")
                    slots.append(slot)
            return HistoryPage(items=tuple(slots), next_start=page.next_start, partial=page.partial)

        return await self.bounded(
            actor,
            sid,
            operation,
            disclose=lambda page: disclosure.history(self.access, actor, sid, page),
            session_id=sid,
        )

    def limits(self) -> ArchiveLimits:
        return ArchiveLimits(
            max_sessions=1,
            max_entries=self.access.limits.max_lookup_entries,
            max_bytes=self.access.limits.max_read_bytes,
        )

    async def write(self, actor: Actor, sid: str) -> None:
        self.access.check()
        sink = self.sink
        if sink is None or not sink.capabilities.writable:
            raise Unsupported("archive_sink_unconfigured")

        async def operation() -> None:
            await self.access.authorize(
                actor,
                sid,
                "x.tether.archive_session",
                session_id=sid,
            )
            if self.access.store_for(actor).workers.busy(sid):
                raise WorkerOutstanding("worker_outstanding")
            snapshot = await self.access.store_for(actor).snapshot(sid, actor.id)
            if snapshot.view.summary.status not in {"idle", "closed"}:
                raise StoreError("archive_session_running")
            transfer = ArchiveTransfer(
                self.access.store_for(actor),
                self.access.history,
                self.access.policy,
                limits=self.limits(),
                observers=self.access.observers,
            )
            bundle = await transfer.export(sid, actor)
            if await self.access.store_for(actor).snapshot(sid, actor.id) != snapshot:
                raise StoreError("archive_session_changed")
            view = snapshot.view.model_copy(
                update={"summary": snapshot.view.summary.model_copy(update={"tier": "archive"})}
            )
            value = ArchivedSession(view=view, bundle=bundle)
            self.access.size(value)
            await self.access.authorize(
                actor,
                sid,
                "x.tether.archive_session",
                session_id=sid,
            )
            await sink.archive(value)

        await self.bounded(actor, sid, operation, session_id=sid)

    async def restore(self, actor: Actor, sid: str) -> SessionSummary:
        source = self.selected()
        if self.capabilities is None or not self.capabilities.restorable:
            raise NotRestorable("not_restorable")

        async def operation() -> SessionSummary:
            await self.access.authorize(
                actor,
                sid,
                "x.tether.restore_session",
                session_id=sid,
            )
            if self.access.store_for(actor).workers.busy(sid):
                raise WorkerOutstanding("worker_outstanding")
            await self.access.authorize(
                actor,
                sid,
                "read_call_payload",
                session_id=sid,
            )
            value = await source.restore(sid, actor=copy_actor(actor))
            self.access.size(value)
            value = value.model_copy(deep=True)
            if value.view.summary.id != sid or value.bundle.session.state.id != sid:
                raise IntegrityError("archive_scope")
            self.summary(actor, value.view.summary)
            if len(value.bundle.session.entries) > self.access.limits.max_lookup_entries:
                raise ReadLimit("read_limit")
            verify_bundle(value.bundle)
            transfer = ArchiveTransfer(
                self.access.store_for(actor),
                self.access.history,
                self.access.policy,
                limits=self.limits(),
                observers=self.access.observers,
            )
            await transfer.restore(value.bundle, actor)
            view = await self.access.store_for(actor).get(sid, actor.id)
            if view is None:
                raise IntegrityError("archive_restore_missing")
            return view.summary

        return await self.bounded(actor, sid, operation, session_id=sid)
