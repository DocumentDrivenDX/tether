"""Accepted executable edits survive a fresh Engine before the first intent."""

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.testing.approval_edits import NarrowCount
from tether.types.execution import Actor, AgentSpec, Answer, CommitUnknown
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_edited_confirmation_restarts_with_the_accepted_executable_digest(tmp_path):
    files = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    tool, actor = DemoTool(), Actor(id="owner")
    spec = AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="",
        model="fixture",
        tools=(tool.name,),
    )

    def build(script):
        return Engine(
            model=ScriptedModel(script),
            history=MessageHistoryAdapter(history),
            history_provider=history,
            tools=(tool,),
            specs=(spec,),
            approval_transforms={"write": NarrowCount()},
            **fabricated_context(),
        )

    first = build([proposal(), final()])
    session = await store.create(actor.id, SessionInit(settings={"approval": "manual"}))
    async with store.turn(session.id, actor.id, turn_id="original") as log:
        pending = (await first.run_turn(actor, log, "Fabricated", spec=spec)).pending[0]
    async with store.turn(session.id, actor.id, turn_id="original") as log:
        append = log.append

        async def before_intent(entries):
            if any(e.key and e.key.startswith("intent:") for e in entries):
                raise CommitUnknown("fabricated_before_intent")
            return await append(entries)

        log.append = before_intent
        with pytest.raises(CommitUnknown):
            await first.answer(
                actor, log, pending.invocation_id, Answer(approved=True, arguments={"count": 1})
            )
    await first.aclose()
    await store.aclose()
    await files.aclose()
    files = await JsonlRoot.open(tmp_path)
    store, history = await JsonlSessionStore.open(files), await JsonlHistory.open(files)
    recovered = build([final()])
    try:
        async with store.turn(session.id, actor.id, turn_id="recovered") as log:
            result = await recovered.resume(actor, log)
            assert result.stop_reason == "final"
            intent = next(e for e in log.entries if e.key == "intent:" + pending.invocation_id)
            assert intent.facts["args_digest"] != pending.args_digest
        assert [args["count"] for _inv, args in tool.calls] == [1]
    finally:
        await recovered.aclose()
        await store.aclose()
        await files.aclose()
