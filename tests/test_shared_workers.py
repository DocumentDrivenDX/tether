"""Actual executor lifetimes survive canceled waiters and Engine replacement."""

import asyncio
from threading import Event

import pytest
from test_resume_engine import build
from test_resume_engine import recovery as recovery

from tether.engine.workers import Workers
from tether.stores.jsonl import JsonlRoot
from tether.types.execution import WorkerOutstanding
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import RootBusy, SessionInit

pytestmark = pytest.mark.anyio


async def test_replacement_engine_and_lease_refuse_until_actual_exit(recovery):
    h = recovery
    run = build(h)
    entered, release = Event(), Event()

    def blocked(ctx, args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test_worker_release")
        return "Fabricated"

    run.tool.run = blocked
    session = await h.store.create(h.actor.id, SessionInit())
    fresh = build(h, tool=run.tool, spec=run.spec)
    leased = []

    async def execute():
        async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
            leased.append(log)
            await run.engine.run_turn(h.actor, log, "Fabricated", spec=run.spec)

    task = asyncio.create_task(execute())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.store.workers.busy(session.id)
        with pytest.raises(WorkerOutstanding):
            async with h.store.turn(session.id, h.actor.id, turn_id="replacement"):
                pytest.fail("a live worker cannot release session ownership")
        with pytest.raises(WorkerOutstanding):
            fresh.engine._enter(h.actor, leased[0])
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*tuple(run.engine.workers.active))
    assert not h.store.workers.busy(session.id)
    async with h.store.turn(session.id, h.actor.id, turn_id="after-exit"):
        pass


async def test_namespace_isolation_and_shutdown_sealing():
    first, second = Workers(1), Workers(1)
    first.scopes["same"] = WorkerLifetime()
    second.scopes["same"] = WorkerLifetime()
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        return release.wait(5)

    waiting = asyncio.create_task(first.call("same", blocked))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
        assert await second.call("same", lambda: "independent") == "independent"
        first.scopes["same"].closed = True
        from tether.types.execution import ExecutionDenied

        with pytest.raises(ExecutionDenied, match="engine_closed"):
            await first.call("same", lambda: None)
    finally:
        release.set()
        assert await waiting
    assert await first.close(0.01)
    assert await second.close(0.01)


@pytest.mark.parametrize("recovery", ["jsonl"], indirect=True)
async def test_root_close_retains_lock_for_another_engines_actual_worker(recovery):
    h = recovery
    root = h.store.files
    root.limits = root.limits.model_copy(update={"shutdown_seconds": 0.01})
    engine = build(h).engine
    engine.workers.scopes["session"] = h.store.workers
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        return release.wait(5)

    waiting = asyncio.create_task(engine.workers.call("session", blocked))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert await root.aclose() is False
        with pytest.raises(RootBusy):
            await JsonlRoot.open(root.path)
    finally:
        release.set()
        await asyncio.gather(*tuple(engine.workers.active))
    reopened = await JsonlRoot.open(root.path)
    assert await reopened.aclose()


async def test_wrong_loop_refuses_before_executor_submission():
    scope = WorkerLifetime()
    assert not scope.busy("same")
    workers = Workers(1)
    workers.scopes["same"] = scope
    executed = Event()

    def foreign_loop():
        async def attempt():
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await workers.call("same", executed.set)

        asyncio.run(attempt())

    await asyncio.to_thread(foreign_loop)
    assert not executed.is_set()
    assert not workers.active
    assert not scope.active
