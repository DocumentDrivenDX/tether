"""Reference-complete export/restore preserves immutable facts and source addresses."""

from __future__ import annotations

import pytest

from tether.engine.governance import AllowAll
from tether.history.jsonl import JsonlHistory
from tether.sessions.archive import ArchiveTransfer, dump_archive, load_archive
from tether.stores.jsonl import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import Message
from tether.types.execution import Actor, NewEntry
from tether.types.history import HistoryRecord
from tether.types.storage import IntegrityError, SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_archive_round_trip_closes_all_references_and_preserves_entry_identity():
    files = MemoryFiles()
    source = await JsonlSessionStore.open(files)
    history = await JsonlHistory.open(files)
    actor = Actor(id="owner")
    session = await source.create(actor.id, SessionInit(ref="fabricated"))
    ref = await history.put(
        actor=actor,
        session_id=session.id,
        key="message",
        record=HistoryRecord(kind="message", message=Message(role="user", content="Fabricated")),
    )
    async with source.turn(session.id, actor.id, turn_id="turn") as log:
        await log.append(
            [
                NewEntry(kind="message", history_ref=ref),
                NewEntry(kind="x.fabricated.unknown", facts={"version": 17}),
            ]
        )
    bundle = await ArchiveTransfer(source, history, AllowAll()).export(session.id, actor)
    assert load_archive(dump_archive(bundle)) == bundle
    target_files = MemoryFiles()
    target = await JsonlSessionStore.open(target_files)
    target_history = await JsonlHistory.open(target_files)
    transfer = ArchiveTransfer(target, target_history, AllowAll())
    await transfer.restore(bundle, actor)
    await transfer.restore(bundle, actor)
    actual = await target.entries(session.id, actor.id, start=0, limit=10)
    assert actual.items == bundle.session.entries
    restored = (await target_history.read([ref], actor=actor))[0].record
    assert restored is not None
    assert restored.model_dump(mode="json") == bundle.records[0]["record"]
    assert (await target.snapshot(session.id, actor.id)).view.summary.message_count == 1
    with pytest.raises(IntegrityError):
        dump_archive(bundle.model_copy(update={"records": ()}))
