"""Real file faults and process exit exercise accepted-prefix recovery and fencing."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.jsonl import root as root_module
from tether.stores.memory import MemoryFiles
from tether.types.execution import CommitUnknown, NewEntry
from tether.types.storage import IntegrityError, SessionInit, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_uncertain_head_commit_requires_reopen_and_replays_complete_batch(
    tmp_path, monkeypatch
):
    root = await JsonlRoot.open(tmp_path)

    def fail(*args):
        raise OSError("fabricated witness failure")

    with monkeypatch.context() as scope:
        scope.setattr(root_module, "save_head", fail)
        with pytest.raises(CommitUnknown):
            await root.append_file("journal", {"fabricated": True})
        with pytest.raises(StoreError, match="writer_unavailable"):
            await root.append_file("journal", {"second": True})
    assert await root.aclose()
    reopened = await JsonlRoot.open(tmp_path)
    try:
        values = [r.value async for r in reopened.scan("journal")]
        assert values == [{"fabricated": True}]
    finally:
        await reopened.aclose()


async def test_missing_acknowledged_witness_is_not_treated_as_empty_or_uncommitted(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    await root.append_file("journal", {"fabricated": True})
    await root.aclose()
    (tmp_path / "journal.head").unlink()
    with pytest.raises(IntegrityError, match="acknowledged_prefix_missing"):
        await JsonlRoot.open(tmp_path)


async def test_canceled_file_writer_retains_lock_and_capacity_until_actual_exit(
    tmp_path, monkeypatch
):
    root = await JsonlRoot.open(tmp_path, limits=StorageLimits(shutdown_seconds=0.001))
    entered, release = Event(), Event()
    original = root._append

    def blocked(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("fabricated worker deadline")
        return original(*args)

    monkeypatch.setattr(root, "_append", blocked)
    task = asyncio.create_task(root.append_file("journal", {"fabricated": True}))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await root.aclose() is False
        from tether.types.storage import RootBusy

        with pytest.raises(RootBusy):
            await JsonlRoot.open(tmp_path)
    finally:
        release.set()
        await asyncio.gather(*tuple(root._active), return_exceptions=True)
        assert await root.aclose()
    reopened = await JsonlRoot.open(tmp_path)
    await reopened.aclose()


async def test_projection_publish_failure_is_unknown_and_stale_indexes_are_unavailable(monkeypatch):
    from tether.types.execution import SessionState
    from tether.types.storage import SessionArchive

    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    init = SessionInit(ref="fabricated")
    session = await store.create("owner", init)
    archive = SessionArchive(
        state=SessionState(id=session.id, owner="owner"), init=init, entries=()
    )
    original = store._publish

    def fail(event):
        if event.operation == "append":
            raise RuntimeError("fabricated publication failure")
        return original(event)

    monkeypatch.setattr(store, "_publish", fail)
    with pytest.raises(CommitUnknown, match="projection_publish_unknown"):
        async with store.turn(session.id, "owner", turn_id="turn") as log:
            await log.append([NewEntry(kind="message")])
    with pytest.raises(StoreError):
        await store.snapshot(session.id, "owner")
    with pytest.raises(StoreError):
        await store.create("owner", init)
    with pytest.raises(StoreError):
        await store.restore(archive, created_at=session.created_at)
    with pytest.raises(StoreError):
        await store.recover(now=session.updated_at)


CHILD = """
import asyncio, json, os, sys
from datetime import UTC, datetime
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import NewEntry
from tether.types.storage import SessionInit
async def main():
    root = await JsonlRoot.open(sys.argv[1])
    store = await JsonlSessionStore.open(root, clock=lambda: datetime(2026,1,1,tzinfo=UTC))
    session = await store.create('owner', SessionInit(ref='fabricated'))
    async with store.turn(session.id, 'owner', turn_id='interrupted') as log:
        await log.append([NewEntry(kind='turn_started', turn_id='interrupted'),
            NewEntry(kind='tool_call', key='intent:invocation', turn_id='interrupted',
                     facts={'invocation_id':'invocation','status':'started','effect':'effectful'})])
        print(json.dumps({'session_id':session.id}), flush=True)
        os._exit(0)
asyncio.run(main())
"""


async def test_actual_process_exit_preserves_intent_and_recovers_interrupted_turn(tmp_path):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    assert process.returncode == 0, stderr.decode()
    import json

    session_id = json.loads(stdout)["session_id"]
    root = await JsonlRoot.open(tmp_path)
    try:
        now = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=31)
        store = await JsonlSessionStore.open(root, clock=lambda: now)
        before = await store.snapshot(session_id, "owner")
        assert before.view.summary.unresolved_effect_count == 1
        assert (await store.recover(now=now))[0].turn_id == "interrupted"
        after = await store.snapshot(session_id, "owner")
        assert after.view.summary.status == "interrupted"
        assert after.view.summary.unresolved_effect_count == 1
        assert await store.recover(now=now) == ()
    finally:
        await root.aclose()


async def test_recovered_complete_tail_gets_a_new_durable_witness(tmp_path, monkeypatch):
    root = await JsonlRoot.open(tmp_path)
    await root.append_file("journal", {"first": True})
    first = (tmp_path / "journal.jsonl").read_bytes()

    def fail(*args):
        raise OSError("fabricated witness failure")

    with monkeypatch.context() as scope:
        scope.setattr(root_module, "save_head", fail)
        with pytest.raises(CommitUnknown):
            await root.append_file("journal", {"second": True})
    await root.aclose()
    reopened = await JsonlRoot.open(tmp_path)
    assert len([r async for r in reopened.scan("journal")]) == 2
    await reopened.aclose()
    (tmp_path / "journal.jsonl").write_bytes(first)
    with pytest.raises(IntegrityError, match="acknowledged_prefix"):
        await JsonlRoot.open(tmp_path)


async def test_canceling_close_does_not_cancel_the_live_writer_future(tmp_path, monkeypatch):
    root = await JsonlRoot.open(tmp_path, limits=StorageLimits(shutdown_seconds=0.02))
    entered, release = Event(), Event()

    def guard():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("fabricated worker deadline")

    task = asyncio.create_task(root.append_file("journal", {"fabricated": True}, guard=guard))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
        closing = asyncio.create_task(root.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert await root.aclose() is False
        from tether.types.storage import RootBusy

        with pytest.raises(RootBusy):
            await JsonlRoot.open(tmp_path)
    finally:
        release.set()
        await task
        assert await root.aclose()


async def test_failed_turn_is_settled_before_its_lease_is_released():
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    session = await store.create("owner", SessionInit())

    async def fail_turn():
        async with store.turn(session.id, "owner", turn_id="failed") as log:
            await log.append([NewEntry(kind="turn_started", turn_id="failed")])
            raise ValueError("fabricated")

    with pytest.raises(ValueError, match="fabricated"):
        await fail_turn()
    snapshot = await store.snapshot(session.id, "owner")
    assert snapshot.view.summary.status == "interrupted"
    assert snapshot.view.summary.active_turn_id is None
    assert await store.recover(now=datetime(2030, 1, 1, tzinfo=UTC)) == ()


async def test_index_budget_refuses_before_committing_or_publishing():
    files = MemoryFiles(StorageLimits(max_index_bytes=2500))
    store = await JsonlSessionStore.open(files)
    session = await store.create("owner", SessionInit())
    async with store.turn(session.id, "owner", turn_id="turn") as log:
        before = await store.snapshot(session.id, "owner")
        with pytest.raises(StoreError, match="journal_index_limit"):
            await log.append([NewEntry(kind="x.fabricated.big", facts={"large": "a" * 2000})])
        assert await store.snapshot(session.id, "owner") == before
        await log.append([NewEntry(kind="x.fabricated.small")])


async def test_startup_work_limit_does_not_silently_truncate_accepted_data(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    await root.append_file("journal", {"first": True})
    await root.append_file("journal", {"second": True})
    await root.aclose()
    with pytest.raises(StoreError, match="startup_batch_limit"):
        await JsonlRoot.open(tmp_path, limits=StorageLimits(max_startup_batches=1))
    root = await JsonlRoot.open(tmp_path)
    assert [v.value async for v in root.scan("journal")] == [{"first": True}, {"second": True}]
    await root.aclose()
