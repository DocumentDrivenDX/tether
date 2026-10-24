"""Reference-complete archive bundles. Metadata-only export never claims restoration."""

from __future__ import annotations

from tether.types import JsonValue
from tether.types.archive import ArchiveLimits
from tether.types.archive_codec import dump_archive as dump_archive
from tether.types.archive_codec import load_archive as load_archive
from tether.types.archive_codec import references, verify_bundle
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    Actor,
    AuthzRequest,
    Decision,
    Entry,
    ExecutionDenied,
    HistoryRef,
    Policy,
    SessionState,
    copy_actor,
)
from tether.types.history import HistoryProvider, StoredHistory
from tether.types.storage import (
    ArchiveBundle,
    EntryPage,
    IntegrityError,
    SessionArchive,
    SessionInit,
    SessionStore,
    StoreError,
)


class ArchiveTransfer:
    def __init__(
        self,
        store: SessionStore,
        history: HistoryProvider,
        authorize: Policy,
        *,
        limits: ArchiveLimits | None = None,
    ) -> None:
        self.store, self.history, self.policy, self.limits = store, history, authorize, limits

    def _bound(self, count: int, size: int) -> None:
        if self.limits is not None and (
            count > self.limits.max_entries or size > self.limits.max_bytes
        ):
            raise StoreError("archive_limit")

    async def _authorize(self, actor: Actor, session_id: str, action: str = "read_session") -> None:
        try:
            decision = Decision.model_validate(
                await self.policy.authorize(
                    AuthzRequest(actor=copy_actor(actor), action=action, resource=session_id)
                )
            )
            allowed = decision.allowed
        except Exception:
            allowed = False
        if not allowed:
            raise ExecutionDenied("archive_denied")

    async def export(self, session_id: str, actor: Actor) -> ArchiveBundle:
        actor = copy_actor(actor)
        await self._authorize(actor, session_id)
        snapshot = await self.store.for_actor(copy_actor(actor)).snapshot(session_id, actor.id)
        if snapshot.view.summary.status not in {"closed", "idle", "waiting", "interrupted"}:
            raise StoreError("archive_session_running")
        entries: list[Entry] = []
        start = 0
        while True:
            page: EntryPage = await self.store.for_actor(copy_actor(actor)).entries(
                session_id, actor.id, start=start, limit=100
            )
            entries.extend(page.items)
            self._bound(
                len(entries), sum(len(canonical(e.model_dump(mode="json"))) for e in entries)
            )
            if page.next_start is None:
                break
            start = page.next_start
        records: list[dict[str, JsonValue]] = []
        refs = {
            canonical(ref.model_dump(mode="json")): ref
            for entry in entries
            for ref in references(entry.model_dump(mode="json"))
        }
        for ref in refs.values():
            stored = await self._record(actor, session_id, ref)
            records.append(stored.model_dump(mode="json"))
            self._bound(
                len(entries),
                sum(len(canonical(e.model_dump(mode="json"))) for e in entries)
                + sum(len(canonical(record)) for record in records),
            )
        if await self.store.for_actor(copy_actor(actor)).snapshot(session_id, actor.id) != snapshot:
            raise StoreError("archive_session_changed")
        summary, settings = snapshot.view.summary, snapshot.view.settings
        init = SessionInit(
            ref=summary.ref,
            spec=summary.spec_id,
            title=summary.title,
            tags=summary.tags,
            settings=settings,
        )
        state = SessionState.model_validate(
            {"id": session_id, "owner": actor.id}
            | {key: value for key, value in settings.items() if key in {"approval", "held"}}
        )
        session = SessionArchive(state=state, init=init, entries=tuple(entries))
        bundle = ArchiveBundle(
            version=1,
            session=session,
            created_at=summary.created_at,
            records=tuple(records),
            sha256="",
        )
        bundle = bundle.model_copy(
            update={"sha256": digest(bundle.model_dump(mode="json", exclude={"sha256"}))}
        )
        self._bound(len(entries), len(canonical(bundle.model_dump(mode="json"))))
        await self._authorize(actor, session_id)
        if any(ref.kind == "model_exchange" for ref in refs.values()):
            await self._authorize(actor, session_id, "read_call_payload")
        return bundle

    async def _record(self, actor: Actor, session_id: str, ref: HistoryRef) -> StoredHistory:
        if ref.owner != actor.id or ref.session_id != session_id:
            raise ExecutionDenied("archive_denied")
        await self._authorize(actor, session_id)
        if ref.kind == "model_exchange":
            await self._authorize(actor, session_id, "read_call_payload")
        values = await self.history.read((ref,), actor=copy_actor(actor))
        if len(values) != 1 or values[0].status != "ready" or values[0].record is None:
            raise StoreError("archive_reference_unavailable")
        record = values[0].record.model_copy(deep=True)
        if (
            values[0].ref != ref
            or record.kind != ref.kind
            or digest(record.model_dump(mode="json")) != ref.sha256
        ):
            raise IntegrityError("archive_reference_integrity")
        return StoredHistory(ref=ref, record=record)

    async def restore(self, bundle: ArchiveBundle, actor: Actor) -> None:
        actor = copy_actor(actor)
        self._bound(len(bundle.session.entries), len(canonical(bundle.model_dump(mode="json"))))
        verify_bundle(bundle)
        if actor.id != bundle.session.state.owner:
            raise ExecutionDenied("archive_denied")
        await self._authorize(actor, bundle.session.state.id, "x.tether.restore_session")
        for raw in bundle.records:
            stored = StoredHistory.model_validate(raw)
            if stored.ref.kind == "model_exchange":
                await self._authorize(actor, bundle.session.state.id, "read_call_payload")
            actual = await self.history.put(
                actor=copy_actor(actor),
                session_id=stored.ref.session_id,
                key=stored.ref.key,
                record=stored.record,
            )
            if actual != stored.ref:
                raise StoreError("archive_provider_incompatible")
        await self._authorize(actor, bundle.session.state.id)
        if any(
            StoredHistory.model_validate(raw).ref.kind == "model_exchange" for raw in bundle.records
        ):
            await self._authorize(actor, bundle.session.state.id, "read_call_payload")
        await self._authorize(actor, bundle.session.state.id, "x.tether.restore_session")
        await self.store.for_actor(copy_actor(actor)).restore(
            bundle.session, created_at=bundle.created_at
        )
