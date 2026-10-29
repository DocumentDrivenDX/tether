"""Direct commands recover their original invocation without model provenance or an Agent loop."""

import pytest
from test_gated_execution import DemoTool

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.memory import MemoryFiles, MemorySessionStore
from tether.types.commands import InvokeTool
from tether.types.execution import Actor, AgentSpec, CommitUnknown
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_before_intent_recovery_keeps_same_command_and_sends_no_model_request():
    files = MemoryFiles()
    store, history = await MemorySessionStore.open(files), await JsonlHistory.open(files)
    tool, model = DemoTool(), ScriptedModel([])
    actor = Actor(id="owner")
    spec = AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="",
        model="fixture",
        tools=(tool.name,),
    )
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        tools=(tool,),
        specs=(spec,),
        **fabricated_context(),
    )
    session = await store.create(actor.id, SessionInit())
    try:
        async with store.turn(session.id, actor.id, turn_id="first") as log:
            append = log.append

            async def before_intent(entries):
                if any(e.key and e.key.startswith("intent:") for e in entries):
                    raise CommitUnknown("fabricated_before_intent")
                return await append(entries)

            log.append = before_intent
            with pytest.raises(CommitUnknown):
                await engine.commands.invoke(
                    actor,
                    log,
                    InvokeTool(request_id="direct", tool_name="write", arguments={"count": 1}),
                    spec=spec,
                )
        async with store.turn(session.id, actor.id, turn_id="recovered") as log:
            result = await engine.resume(actor, log)
            assert result.stop_reason == "final"
            assert len([e for e in log.entries if e.kind == "tool_command"]) == 1
            assert not any(e.kind in {"model_call", "model_attempt"} for e in log.entries)
        assert len(tool.calls) == 1
        assert not model.requests
    finally:
        await engine.aclose()
        await store.aclose()


@pytest.mark.parametrize("safe", [False, True])
async def test_after_effect_recovery_keeps_invocation_and_never_replays_unsafe(safe):
    files = MemoryFiles()
    store, history = await MemorySessionStore.open(files), await JsonlHistory.open(files)
    tool, model, actor = DemoTool(), ScriptedModel([]), Actor(id="owner")
    tool.replay = "safe" if safe else "unsafe"
    effects = set()
    dispatch = tool.run

    async def idempotent(ctx, args):
        effects.add(ctx.invocation_id)
        return await dispatch(ctx, args)

    tool.run = idempotent
    spec = AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="",
        model="fixture",
        tools=(tool.name,),
    )
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(history),
        history_provider=history,
        tools=(tool,),
        specs=(spec,),
        **fabricated_context(),
    )
    session = await store.create(actor.id, SessionInit())
    try:
        async with store.turn(session.id, actor.id, turn_id="first") as log:
            append = log.append

            async def lose_result(entries):
                if any(e.key and e.key.startswith("result:") for e in entries):
                    raise CommitUnknown("fabricated_after_effect")
                return await append(entries)

            log.append = lose_result
            with pytest.raises(CommitUnknown):
                await engine.commands.invoke(
                    actor,
                    log,
                    InvokeTool(request_id="direct", tool_name="write", arguments={"count": 1}),
                    spec=spec,
                )
        async with store.turn(session.id, actor.id, turn_id="recovered") as log:
            result = await engine.resume(actor, log)
            assert result.stop_reason == ("final" if safe else "recovery_blocked")
            assert len([e for e in log.entries if e.key and e.key.startswith("intent:")]) == 1
        assert len(tool.calls) == (2 if safe else 1)
        assert len(effects) == 1
        assert not model.requests
        assert len({invocation for invocation, _args in tool.calls}) == 1
    finally:
        await engine.aclose()
        await store.aclose()
