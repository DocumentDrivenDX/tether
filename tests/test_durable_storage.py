"""Local JSONL and volatile implementations share the same storage conformance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import Message
from tether.types.execution import Actor, EntryConflict, NewEntry
from tether.types.history import HistoryRecord
from tether.types.storage import (
    IntegrityError,
    LeaseLost,
    RootBusy,
    SessionInit,
    TurnInProgress,
    UnknownSession,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=["memory", "jsonl"])
async def files(request, tmp_path):
    root = await JsonlRoot.open(tmp_path) if request.param == "jsonl" else MemoryFiles()
    try:
        yield root
    finally:
        if isinstance(root, JsonlRoot):
            await root.aclose()


async def test_session_store_conformance_owner_keys_atomic_snapshots_and_paging(files):
    store = await JsonlSessionStore.open(files)
    session = await store.create("owner", SessionInit(ref="fabricated"))
    assert await store.create("owner", SessionInit(ref="fabricated")) == session
    assert (await store.create("other", SessionInit(ref="fabricated"))).id != session.id
    assert await store.get(session.id, "other") is None
    with pytest.raises(UnknownSession):
        await store.snapshot(session.id, "other")
    value = NewEntry(kind="turn_started", turn_id="turn", key="request:one")
    async with store.turn(session.id, "owner", turn_id="turn") as log:
        first = await log.append([value])
        assert await log.append([value]) == first
        snapshot = await store.snapshot(session.id, "owner")
        with pytest.raises(EntryConflict):
            await log.append(
                [NewEntry(kind="message"), value.model_copy(update={"kind": "changed"})]
            )
        assert await store.snapshot(session.id, "owner") == snapshot
        await log.append(
            [NewEntry(kind="turn_ended", turn_id="turn", facts={"stop_reason": "final"})]
        )
        await log.heartbeat()
    snapshot = await store.snapshot(session.id, "owner")
    assert snapshot.through_index == 1
    assert snapshot.view.summary.status == "idle"
    page = await store.entries(session.id, "owner", start=0, limit=1)
    assert page.items[0].index == 0
    assert page.next_start == 1
    assert (
        await store.entries(session.id, "owner", start=1, limit=2, reverse=True)
    ).next_start is None
    assert snapshot.view.summary.entry_count == 2
    snapshot.view.settings["mutated"] = True
    assert "mutated" not in (await store.snapshot(session.id, "owner")).view.settings


async def test_history_provider_conformance_immutable_scoped_and_message_adapter(files):
    history = await JsonlHistory.open(files)
    actor = Actor(id="owner")
    record = HistoryRecord(kind="message", message=Message(role="user", content="fabricated"))
    ref = await history.put(actor=actor, session_id="session", key="one", record=record)
    assert await history.put(actor=actor, session_id="session", key="one", record=record) == ref
    assert (await history.read([ref], actor=actor))[0].record == record
    assert (await history.read([ref], actor=Actor(id="other")))[0].status == "denied"
    assert (await history.read([ref.model_copy(update={"sha256": "changed"})], actor=actor))[
        0
    ].status == "integrity_error"
    with pytest.raises(EntryConflict):
        await history.put(
            actor=actor,
            session_id="session",
            key="one",
            record=HistoryRecord(kind="summary", summary="changed"),
        )
    adapter = MessageHistoryAdapter(history)
    assert await adapter.read(ref, actor=actor, session_id="session") == record.message


async def test_local_root_one_os_writer_and_reopen_reconstructs(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    history = await JsonlHistory.open(root)
    session = await store.create("owner", SessionInit(ref="fabricated"))
    actor = Actor(id="owner")
    ref = await history.put(
        actor=actor,
        session_id=session.id,
        key="source",
        record=HistoryRecord(kind="message", message=Message(role="user", content="fabricated")),
    )
    async with store.turn(session.id, "owner", turn_id="turn") as log:
        await log.append([NewEntry(kind="message", history_ref=ref)])
    expected = await store.snapshot(session.id, "owner")
    with pytest.raises(RootBusy):
        await JsonlRoot.open(tmp_path)
    with pytest.raises(RootBusy):
        await JsonlSessionStore.open(root)
    await root.aclose()
    reopened = await JsonlRoot.open(tmp_path)
    try:
        restored = await JsonlSessionStore.open(reopened)
        restored_history = await JsonlHistory.open(reopened)
        assert await restored.snapshot(session.id, "owner") == expected
        assert (await restored_history.read([ref], actor=actor))[0].status == "ready"
    finally:
        await reopened.aclose()


async def test_fencing_takeover_and_expired_heartbeat(files):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = await JsonlSessionStore.open(files, clock=lambda: now[0])
    session = await store.create("owner", SessionInit())
    async with store.turn(session.id, "owner", turn_id="first") as old:
        await old.append([NewEntry(kind="turn_started", turn_id="first")])
        with pytest.raises(TurnInProgress):
            async with store.turn(session.id, "owner", turn_id="competing"):
                pass
        now[0] += timedelta(seconds=31)
        recovered = await store.recover(now=now[0])
        assert recovered[0].turn_id == "first"
        assert await store.recover(now=now[0]) == ()
        async with store.turn(session.id, "owner", turn_id="second") as fresh:
            with pytest.raises(LeaseLost):
                await old.append([NewEntry(kind="message")])
            with pytest.raises(LeaseLost):
                await old.heartbeat()
            await fresh.append([NewEntry(kind="turn_started", turn_id="second")])


async def test_torn_unacknowledged_tail_is_reported_but_acknowledged_damage_refused(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    await root.append_file("journal", {"fabricated": True})
    await root.aclose()
    path = tmp_path / "journal.jsonl"
    original = path.read_bytes()
    path.write_bytes(original + b'{"torn":')
    reopened = await JsonlRoot.open(tmp_path)
    assert reopened.recovery_notices[0]["torn_bytes"] == 8
    assert path.read_bytes() == original
    await reopened.aclose()
    path.write_bytes(original[:-1])
    with pytest.raises(IntegrityError, match="acknowledged_prefix"):
        await JsonlRoot.open(tmp_path)


async def test_jsonl_chain_corruption_fails_closed(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    await root.append_file("journal", {"fabricated": True})
    await root.aclose()
    path = tmp_path / "journal.jsonl"
    path.write_bytes(path.read_bytes().replace(b"true", b"null"))
    with pytest.raises(IntegrityError):
        await JsonlRoot.open(tmp_path)
