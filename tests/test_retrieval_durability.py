"""Durable Agent resume and shutdown keep read observations outside storage authority."""

import asyncio
from contextlib import suppress

import pytest
from test_gated_execution import final, proposal
from test_resume_engine import build, interrupted
from test_resume_engine import recovery as recovery
from test_retrieval_observers import ReadTool, observers, retrievals

from tether import Tether
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, AgentSpec
from tether.types.telemetry import TelemetryLimits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_fresh_agent_resume_read_uses_original_source_and_current_phase(
    recovery, monkeypatch
):
    h = recovery
    tool = ReadTool(lambda: "Fabricated")
    run = build(h, safe=True, tool=tool)
    sid = await interrupted(h, run, "proposal", monkeypatch)
    sink, tracer = observers()
    fresh = Engine(
        model=run.model,
        history=MessageHistoryAdapter(h.provider),
        history_provider=h.provider,
        model_limits=h.limits,
        tools=(tool,),
        specs=(run.spec,),
        audit=sink,
        tracer=tracer,
    )
    h.engines.append(fresh)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.resume(h.actor.model_copy(update={"principal": "current"}), log)
    assert result.stop_reason == "final"
    assert await fresh.aclose()
    row = retrievals(sink)[0]
    assert row.context.principal == "current"
    assert row.detail["source_turn_id"] == "original"
    assert row.detail["phase_turn_id"] == "continued"
    entries = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    gate = entries[row.detail["authorization_position"]]
    assert gate.kind == "authorization"
    assert gate.facts["action"] == "retrieve"
    assert gate.facts["allowed"] is True


async def test_retrieval_with_hung_sink_finishes_and_releases_storage_root(
    tmp_path, limits, client_limits
):
    entered, release = asyncio.Event(), asyncio.Event()

    class Sink:
        async def emit(self, **kwargs):
            entered.set()
            while not release.is_set():
                with suppress(asyncio.CancelledError):
                    await release.wait()

    root = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(root), await JsonlHistory.open(root)
    tool = ReadTool(lambda: "Fabricated")
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
    )
    engine = Engine(
        model=ScriptedModel([proposal(), final()]),
        history=MessageHistoryAdapter(history),
        history_provider=history,
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
        audit=Sink(),
        telemetry_limits=TelemetryLimits(
            delivery_timeout_seconds=0.01, shutdown_timeout_seconds=0.01
        ),
    )
    app = Tether(engine=engine, store=store, history=history, limits=client_limits, resume="off")
    await app.__aenter__()
    try:
        client = app.client(Actor(id="owner"))
        made = await client.create(spec=spec.id)
        handle = await client.send(made.id, "Fabricated")
        assert (await asyncio.wait_for(handle.result(), 2)).stop_reason == "final"
        assert len(tool.calls) == 1
        await asyncio.wait_for(entered.wait(), 1)
        assert not await app.aclose()
        reopened = await JsonlRoot.open(tmp_path)
        assert await reopened.aclose()
    finally:
        release.set()
        if not app.closed:
            await app.aclose()
        await asyncio.wait_for(engine.wait_observers_closed(), 1)
        if app._cleanup is not None:
            await asyncio.wait_for(app._cleanup, 1)
        assert app.drained
        assert await root.aclose()
