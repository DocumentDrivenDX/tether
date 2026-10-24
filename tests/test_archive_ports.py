"""Archive source/sink conformance over actual Agent journal and reference closure."""

import pytest
from test_resume_engine import build
from test_resume_engine import recovery as recovery

from tether.engine.governance import AllowAll
from tether.history.jsonl import JsonlHistory
from tether.sessions.archive import ArchiveTransfer
from tether.stores.archive import InMemoryArchive
from tether.stores.jsonl import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.archive import (
    ArchiveCapabilities,
    ArchivedSession,
    ArchiveLimits,
    ArchiveReadError,
)
from tether.types.execution import Actor, EntryConflict
from tether.types.storage import SessionInit, SessionPatch, SessionQuery, StoreError

pytestmark = pytest.mark.anyio


def archive():
    return InMemoryArchive(ArchiveLimits(max_sessions=10, max_entries=1000, max_bytes=1048576))


async def snapshot(h, sid):
    bundle = await ArchiveTransfer(h.store, h.provider, AllowAll()).export(sid, h.actor)
    view = await h.store.get(sid, h.actor.id)
    return ArchivedSession(
        view=view.model_copy(
            update={"summary": view.summary.model_copy(update={"tier": "archive"})}
        ),
        bundle=bundle,
    )


@pytest.mark.parametrize("state", ["final", "waiting", "unknown", "closed"])
async def test_authoritative_archive_round_trip_preserves_original_state(recovery, state):
    h = recovery
    run = build(h)
    session = await h.store.create(
        h.actor.id, SessionInit(settings={"approval": "manual" if state == "waiting" else "auto"})
    )
    if state == "unknown":

        def fail(ctx, args):
            raise RuntimeError("fabricated_effect_failure")

        run.tool.run = fail
    async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
        await run.engine.run_turn(h.actor, log, "Fabricated", spec=run.spec)
    if state == "closed":
        async with h.store.turn(session.id, h.actor.id, turn_id="close") as log:
            await run.engine.close_session(h.actor, log)
    original = await snapshot(h, session.id)
    sink = archive()
    await sink.archive(original)
    await sink.archive(original)
    view = await sink.get(session.id, actor=h.actor)
    assert view is not None
    assert view == original.view
    assert view.summary.tier == "archive"
    assert not view.summary.partial
    page = await sink.entries(session.id, actor=h.actor, start=0, limit=1000, reverse=False)
    assert page.items == original.bundle.session.entries
    assert not page.partial
    foreign = Actor(id="other")
    assert await sink.get(session.id, actor=foreign) is None
    assert not (
        await sink.list(SessionQuery(owner=foreign.id), cursor=None, limit=10, actor=foreign)
    ).items
    refs = [e.history_ref for e in page.items if e.history_ref is not None]
    values = await sink.read(refs, actor=h.actor)
    assert values
    assert all(v.status == "ready" for v in values)
    assert all(v.status == "denied" for v in await sink.read(refs, actor=foreign))
    recovered = await sink.restore(session.id, actor=h.actor)
    await restored_state(h, session.id, recovered.bundle, view, page.items)


async def restored_state(h, sid, bundle, view, entries):
    target_files = MemoryFiles()
    target = await JsonlSessionStore.open(target_files)
    history = await JsonlHistory.open(target_files)
    try:
        await ArchiveTransfer(target, history, AllowAll()).restore(bundle, h.actor)
        restored = await target.get(sid, h.actor.id)
        assert restored is not None
        assert restored.summary.status == view.summary.status
        assert restored.summary.unresolved_effect_count == view.summary.unresolved_effect_count
        assert restored.pending == view.pending
        assert (await target.entries(sid, h.actor.id, start=0, limit=1000)).items == entries
    finally:
        assert await target.aclose()


async def test_archive_cursor_is_tier_bound_and_conflicting_snapshot_refuses(recovery):
    h, sink = recovery, archive()
    sessions = [await h.store.create(h.actor.id, SessionInit()) for _ in range(2)]
    for session in sessions:
        await sink.archive(await snapshot(h, session.id))
    query = SessionQuery(owner=h.actor.id)
    page = await sink.list(query, cursor=None, limit=1, actor=h.actor)
    assert page.next
    assert len((await sink.list(query, cursor=page.next, limit=1, actor=h.actor)).items) == 1
    with pytest.raises(ValueError, match="invalid_cursor"):
        await h.store.list(query, cursor=page.next, limit=1)
    live = await h.store.list(query, limit=1)
    with pytest.raises(ValueError, match="invalid_cursor"):
        await sink.list(query, cursor=live.next, limit=1, actor=h.actor)
    original = await snapshot(h, sessions[0].id)
    await h.store.update(sessions[0].id, h.actor.id, SessionPatch(title="Changed"))
    current = await snapshot(h, sessions[0].id)
    await sink.archive(current)
    with pytest.raises(EntryConflict):
        await sink.archive(original)
    tiny = InMemoryArchive(ArchiveLimits(max_sessions=1, max_entries=1000, max_bytes=1))
    with pytest.raises(StoreError, match="archive_limit"):
        await tiny.archive(current)


def test_derived_capabilities_cannot_claim_authoritative_closure():
    with pytest.raises(ValueError, match="derived_archive_capabilities"):
        ArchiveCapabilities(
            kind="derived", writable=False, lossless=True, restorable=False, content="full"
        )
    for status in ("pending", "denied", "unavailable"):
        error = ArchiveReadError(status)
        assert error.status == status
        assert str(error) == "archive_" + status


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("field", ["ref", "title", "settings", "created_at"])
async def test_restore_identical_facts_do_not_hide_conflicting_identity(recovery, populated, field):
    from datetime import timedelta

    from tether.types.execution import NewEntry
    from tether.types.storage import SessionArchive

    h = recovery
    session = await h.store.create(h.actor.id, SessionInit(ref="stable", title="Original"))
    if populated:
        async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
            await log.append([NewEntry(kind="x.fabricated.fact", facts={"value": 3})])
    value = await snapshot(h, session.id)
    original = value.bundle.session
    created_at = value.bundle.created_at
    assert await h.store.restore(original, created_at=created_at) == value.view.summary.model_copy(
        update={"tier": "live"}
    )
    patch = {"ref": "different", "title": "Different", "settings": {"extra": True}}
    init = (
        original.init.model_copy(update={field: patch[field]})
        if field != "created_at"
        else original.init
    )
    candidate = SessionArchive(state=original.state, init=init, entries=original.entries)
    at = created_at + timedelta(seconds=1) if field == "created_at" else created_at
    before = await h.store.snapshot(session.id, h.actor.id)
    with pytest.raises(EntryConflict, match="archive_restore_conflict"):
        await h.store.restore(candidate, created_at=at)
    assert await h.store.snapshot(session.id, h.actor.id) == before
