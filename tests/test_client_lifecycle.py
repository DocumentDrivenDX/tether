"""Async composition retains ownership when a real worker outlives bounded shutdown."""

from __future__ import annotations

import asyncio
from threading import Event

import pytest
from test_gated_execution import DemoTool, proposal

from tether import Tether
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import Actor, AgentSpec, ExecutionLimits
from tether.types.storage import RootBusy, SessionInit, StoreError, Unsupported

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_context_seals_clients_and_store_and_refuses_wrong_loop(limits, client_limits):
    files = MemoryFiles()
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    engine = Engine(
        model=ScriptedModel([]), history=MessageHistoryAdapter(history), model_limits=limits
    )
    actor = Actor(id="owner")
    session = await store.create(actor.id, SessionInit())
    app = Tether(engine=engine, store=store, history=history, limits=client_limits, resume="off")
    client = app.client(actor)
    with pytest.raises(RuntimeError, match="tether_not_active"):
        await client.get(session.id)
    async with app:
        assert (await client.get(session.id)).summary.id == session.id

        async def wrong_loop():
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                await client.get(session.id)

        await asyncio.to_thread(asyncio.run, wrong_loop())
    assert app.drained
    with pytest.raises(RuntimeError, match="tether_not_active"):
        await client.get(session.id)
    with pytest.raises(StoreError):
        await store.get(session.id, actor.id)
    assert await app.aclose()


async def test_volatile_profile_requires_explicit_recovery_off(limits, client_limits):
    files = MemoryFiles()
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    engine = Engine(
        model=ScriptedModel([]), history=MessageHistoryAdapter(history), model_limits=limits
    )
    with pytest.raises(Unsupported, match="unsupported_store"):
        Tether(engine=engine, store=store, history=history, limits=client_limits)
    assert await engine.aclose()
    assert await store.aclose()


async def test_close_keeps_root_owned_until_actual_tool_exit(
    tmp_path, limits, client_limits, monkeypatch
):
    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    actor, tool = Actor(id="owner"), DemoTool()
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()

    def blocked(ctx, args):
        loop.call_soon_threadsafe(entered.set)
        release.wait()  # Always released by the test's finally, never by a fixture timer.
        return "Fabricated effect completed"

    monkeypatch.setattr(tool, "run", blocked)
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
    )
    engine = Engine(
        model=ScriptedModel([proposal()]),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
        limits=ExecutionLimits(shutdown_timeout_seconds=0.001),
    )
    app = Tether(engine=engine, store=store, history=history, limits=client_limits)
    await app.__aenter__()
    session = await store.create(actor.id, SessionInit())

    async def run():
        async with store.turn(session.id, actor.id, turn_id="turn") as log:
            await engine.run_turn(actor, log, "Fabricated", spec=spec)

    task = asyncio.create_task(run())
    try:
        await actual_entry(entered, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await app.aclose() is False
        with pytest.raises(RootBusy):
            await JsonlRoot.open(tmp_path)
        assert engine.workers.busy(session.id)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await app.aclose()
        if app._cleanup is not None:
            await asyncio.wait_for(asyncio.shield(app._cleanup), 3)
        assert await root.aclose()
    assert app.drained
    await assert_rebuilt_unknown(tmp_path, session.id, actor.id)


async def assert_rebuilt_unknown(path, session_id, owner):
    reopened = await JsonlRoot.open(path)
    try:
        rebuilt = await JsonlSessionStore.open(reopened)
        summary = (await rebuilt.snapshot(session_id, owner)).view.summary
        assert summary.unresolved_effect_count == 1
    finally:
        assert await reopened.aclose()


@pytest.mark.parametrize(
    "profile",
    [
        {"durability": "none"},
        {"durability": "process"},
        {"durability": "external"},
        {"delivery": "eventual"},
        {"writable": False},
        {"coverage": frozenset({"message"})},
    ],
)
async def test_recovery_refuses_weaker_or_unqualified_history(
    tmp_path, limits, client_limits, profile, monkeypatch
):
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    files = MemoryFiles()
    history = await JsonlHistory.open(files)
    history.capabilities = history.capabilities.model_copy(update={"durability": "os"} | profile)
    engine = Engine(
        model=ScriptedModel([]), history=MessageHistoryAdapter(history), model_limits=limits
    )
    recovered = False

    async def forbidden_recover(*, now):
        nonlocal recovered
        recovered = True
        raise AssertionError("recovery ran before profile validation")

    monkeypatch.setattr(store, "recover", forbidden_recover)
    try:
        with pytest.raises(Unsupported, match="unsupported_history_recovery"):
            Tether(engine=engine, store=store, history=history, limits=client_limits)
        assert not recovered
    finally:
        assert await engine.aclose()
        assert await store.aclose()
        assert await files.aclose()


async def actual_entry(entered, turn):
    # Entry is a barrier, not a filesystem startup latency assertion.
    ready = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait((ready, turn), timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if turn in done:
            await turn
            raise AssertionError("turn_finished_before_tool_entry")
        assert ready in done
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
