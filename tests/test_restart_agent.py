"""Actual Agent continuation rebuilds from accepted facts with no SDK checkpoint cache."""

from __future__ import annotations

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, AgentSpec, Answer
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_approval_after_root_and_engine_restart_runs_bound_invocation_once(tmp_path, limits):
    actor, tool = Actor(id="owner"), DemoTool()
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated.",
        model="demo",
        tools=(tool.name,),
    )
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    history = MessageHistoryAdapter(await JsonlHistory.open(root))
    session = await store.create(actor.id, SessionInit(settings={"approval": "manual"}))
    engine = Engine(
        model=ScriptedModel([proposal()]),
        history=history,
        model_limits=limits,
        attempt_factory=ListAttemptRecorder,
        tools=(tool,),
        specs=(spec,),
    )
    async with store.turn(session.id, actor.id, turn_id="initial") as log:
        pending = await engine.run_turn(actor, log, "Fabricated", spec=spec)
    assert pending.stop_reason == "confirmation_required"
    assert not tool.calls
    invocation_id = pending.pending[0].invocation_id
    assert await engine.aclose()
    await root.aclose()
    root = await JsonlRoot.open(tmp_path)
    try:
        store = await JsonlSessionStore.open(root)
        history = MessageHistoryAdapter(await JsonlHistory.open(root))
        rebuilt = Engine(
            model=ScriptedModel([final()]),
            history=history,
            model_limits=limits,
            attempt_factory=ListAttemptRecorder,
            tools=(tool,),
            specs=(spec,),
        )
        async with store.turn(session.id, actor.id, turn_id="answer") as log:
            result = await rebuilt.answer(actor, log, invocation_id, Answer(approved=True))
        assert result is not None
        assert result.stop_reason == "final"
        assert len(tool.calls) == 1
        assert tool.calls[0][0] == invocation_id
        snapshot = await store.snapshot(session.id, actor.id)
        assert snapshot.view.summary.pending is False
        assert snapshot.view.summary.unresolved_effect_count == 0
        assert await rebuilt.aclose()
    finally:
        await root.aclose()


async def test_default_recorder_prepares_accepted_attempt_before_model_response(limits):
    from tether.stores.memory import MemoryFiles
    from tether.types import TokenUsage

    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    provider = await JsonlHistory.open(files)
    actor = Actor(id="owner")
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Fabricated", model="demo")
    response = final().model_copy(update={"usage": TokenUsage(input_tokens=7, output_tokens=3)})
    session = await store.create(actor.id, SessionInit())
    engine = Engine(
        model=ScriptedModel([response]),
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        model_limits=limits,
    )
    async with store.turn(session.id, actor.id, turn_id="turn") as log:
        result = await engine.run_turn(actor, log, "Fabricated", spec=spec)
        attempts = [e for e in log.entries if e.kind == "model_attempt"]
        receipts = [e for e in log.entries if e.kind == "model_receipt"]
        call = next(e for e in log.entries if e.kind == "model_call")
        assert result.stop_reason == "final"
        assert len(attempts) == len(receipts) == 1
        assert attempts[0].index < receipts[0].index < call.index
    summary = (await store.snapshot(session.id, actor.id)).view.summary
    assert summary.usage.input_tokens == 7
    assert summary.usage.output_tokens == 3
    assert summary.usage_complete
    assert await engine.aclose()


async def make_pending(limits, *, max_steps=20, clock=None, execution_limits=None, batch=False):
    from datetime import UTC, datetime

    from tether.stores.memory import MemoryFiles
    from tether.types import ModelResponse

    clock = clock or (lambda: datetime.now(UTC))
    actor, tool = Actor(id="owner"), DemoTool()
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
        max_steps=max_steps,
    )
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files, clock=clock)
    provider = await JsonlHistory.open(files)
    session = await store.create(actor.id, SessionInit(settings={"approval": "manual"}))
    proposed = proposal()
    if batch:
        proposed = ModelResponse(
            tool_calls=(*proposed.tool_calls, *proposal(call_id="second").tool_calls),
            finish_reason="tool_calls",
        )
    engine = Engine(
        model=ScriptedModel([proposed]),
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
        clock=clock,
        limits=execution_limits,
    )
    async with store.turn(session.id, actor.id, turn_id="initial") as log:
        pending = await engine.run_turn(actor, log, "Fabricated", spec=spec)
    assert await engine.aclose()
    return actor, tool, spec, store, provider, session, pending


@pytest.mark.parametrize(
    "fault", ["read_revoked", "tool_changed", "spec_missing", "source_missing"]
)
async def test_restart_cannot_bypass_current_reads_or_pinned_bindings(limits, fault):
    from test_gated_execution import MutablePolicy

    from tether.types.execution import ExecutionDenied

    actor, tool, spec, store, provider, session, pending = await make_pending(limits)
    policy = MutablePolicy()
    if fault == "read_revoked":
        policy.deny.add("read_session")
    elif fault == "tool_changed":
        tool.version = "2"
    elif fault == "source_missing":
        provider._index.clear()
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=() if fault == "spec_missing" else (spec,),
        policy=policy,
    )
    async with store.turn(session.id, actor.id, turn_id="answer") as log:
        with pytest.raises(ExecutionDenied):
            await engine.answer(actor, log, pending.pending[0].invocation_id, Answer(approved=True))
    assert not tool.calls
    assert await engine.aclose()


async def test_restart_preserves_the_original_step_limit(limits):
    actor, tool, spec, store, provider, session, pending = await make_pending(limits, max_steps=1)
    model = ScriptedModel([final()])
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
    )
    async with store.turn(session.id, actor.id, turn_id="answer") as log:
        result = await engine.answer(
            actor, log, pending.pending[0].invocation_id, Answer(approved=True)
        )
    assert result is not None
    assert result.stop_reason == "step_limit"
    assert len(tool.calls) == 1
    assert not model.requests
    assert await engine.aclose()


async def test_new_turn_after_restart_supersedes_pending_without_the_old_runner(limits):
    actor, tool, spec, store, provider, session, _ = await make_pending(limits)
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
    )
    async with store.turn(session.id, actor.id, turn_id="new") as log:
        result = await engine.run_turn(actor, log, "New fabricated request", spec=spec)
    assert result.stop_reason == "final"
    assert not tool.calls
    assert (await store.snapshot(session.id, actor.id)).view.pending is None
    assert await engine.aclose()


async def test_batch_approval_can_restart_between_each_answer(limits):
    actor, tool, spec, store, provider, session, first = await make_pending(limits, batch=True)
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
    )
    async with store.turn(session.id, actor.id, turn_id="answer-one") as log:
        second = await engine.answer(
            actor, log, first.pending[0].invocation_id, Answer(approved=True)
        )
    assert second is not None
    assert second.stop_reason == "confirmation_required"
    assert len(tool.calls) == 1
    assert await engine.aclose()
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(provider),
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
    )
    async with store.turn(session.id, actor.id, turn_id="answer-two") as log:
        result = await engine.answer(
            actor, log, second.pending[0].invocation_id, Answer(approved=True)
        )
    assert result is not None
    assert result.stop_reason == "final"
    assert len(tool.calls) == 2
    assert tool.calls[0][0] != tool.calls[1][0]
    assert await engine.aclose()


async def test_default_physical_attempt_fencing_prevents_egress_after_expiry(limits):
    from datetime import UTC, datetime, timedelta

    from tether.stores.memory import MemoryFiles
    from tether.types.canonical import digest
    from tether.types.storage import LeaseLost

    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    files = MemoryFiles()
    store = await JsonlSessionStore.open(files, clock=lambda: now[0])
    history = await JsonlHistory.open(files)
    actor = Actor(id="owner")
    session = await store.create(actor.id, SessionInit())
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Fabricated", model="demo")
    egress = []

    class ExpiredModel:
        provider = "fabricated"

        async def stream(self, request):
            assert request.attempts is not None
            identity = await request.attempts.reserve()
            wire = request.model_dump(mode="json")
            now[0] += timedelta(seconds=31)
            await request.attempts.start(identity, request_digest=digest(wire), wire_request=wire)
            egress.append(True)
            yield final()

    engine = Engine(
        model=ExpiredModel(),
        history=MessageHistoryAdapter(history),
        model_limits=limits,
        clock=lambda: now[0],
    )
    async with store.turn(session.id, actor.id, turn_id="turn") as log:
        with pytest.raises(LeaseLost):
            await engine.run_turn(actor, log, "Fabricated", spec=spec)
    assert not egress
    assert (await store.snapshot(session.id, actor.id)).view.summary.status == "interrupted"
    assert await engine.aclose()
