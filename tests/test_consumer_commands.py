"""Direct caller operations retain gates, durable retries and model-free provenance."""

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

import pytest
from test_gated_execution import DemoTool, final

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.client import RequestConflict
from tether.types.execution import Actor, AgentSpec
from tether.types.preparation import PreparationResult

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def composition(
    path,
    tool,
    *,
    model=None,
    prepare=None,
    settlements=(),
    guards=(),
    profiles=(),
    identity_mode: Literal["caller", "application", "anonymous"] = "caller",
):
    files = await JsonlRoot.open(path)
    try:
        history = await JsonlHistory.open(files)
        definition = AgentSpec(
            id="fixture",
            version="1",
            title="Fixture",
            instructions="",
            model="fixture",
            tools=(tool.name,),
        )
        adapter = model if model is not None else ScriptedModel([])
        engine = Engine(
            model=adapter,
            identity_mode=identity_mode,
            history=MessageHistoryAdapter(history),
            history_provider=history,
            tools=(tool,),
            specs=(definition,),
            prepare=prepare,
            settlements=settlements,
            guards=guards,
            auxiliary_profiles=profiles,
            **fabricated_context(),
        )
        async with Tether(
            engine=engine,
            store=await JsonlSessionStore.open(files),
            history=history,
            default_spec=definition.id,
        ) as app:
            yield app.client(Actor(id="owner")), app, adapter
    finally:
        await files.aclose()


async def test_direct_tool_has_no_model_or_assistant_provenance_and_retry_survives_reopen(tmp_path):
    tool = DemoTool()
    async with composition(tmp_path, tool) as (client, _app, model):
        session = await client.create()
        first, second = await asyncio.gather(
            *[
                client.invoke_tool(session.id, "write", {"count": "3"}, request_id="direct")
                for _ in range(2)
            ]
        )
        assert first.turn_id == second.turn_id
        result = await first.result()
        assert result.messages[0].content == "Wrote 3"
        assert len(tool.calls) == 1
        assert tool.calls[0][1]["count"] == 3
        entries = (await client.entries(session.id, start=0, limit=100)).items
        assert not any(e.kind in {"model_call", "model_attempt", "model_receipt"} for e in entries)
        assert [e.facts["origin"] for e in entries if e.kind == "tool_command"] == ["client"]
        assert not any(e.facts.get("role") == "assistant" for e in entries)
        assert not model.requests
        with pytest.raises(RequestConflict):
            await client.invoke_tool(session.id, "write", {"count": 4}, request_id="direct")
    async with composition(tmp_path, tool, model=ScriptedModel([final()])) as (client, _app, model):
        handle = await client.invoke_tool(session.id, "write", {"count": "3"}, request_id="direct")
        assert handle.turn_id == first.turn_id
        assert (await handle.result()).messages == result.messages
        assert len(tool.calls) == 1
        assert not model.requests
        # Standalone tool results must not become orphan tool messages in later model context.
        await (await client.send(session.id, "Next fabricated request")).result()
        assert all(message.role != "tool" for message in model.requests[0].messages)


async def test_direct_approval_required_refuses_without_intent_or_dispatch(tmp_path):
    tool = DemoTool()
    tool.always_ask = True
    async with composition(tmp_path, tool) as (client, _app, model):
        session = await client.create()
        result = await (
            await client.invoke_tool(session.id, "write", {"count": 3}, request_id="needs-approval")
        ).result()
        assert result.messages[0].content == "approval_required"
        assert tool.calls == []
        assert not model.requests
        entries = (await client.entries(session.id, start=0, limit=100)).items
        assert not any(e.key and e.key.startswith(("intent:", "pending:")) for e in entries)


class PausedPreparation:
    def __init__(self, state: Literal["ready", "needs_input", "refused"] = "ready"):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls = 0
        self.state = state

    async def prepare(self, request):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return PreparationResult.model_validate({"state": self.state, "reason": "fabricated"})


@pytest.mark.parametrize("state", ["ready", "needs_input", "refused"])
async def test_preparation_runs_once_after_acceptance_and_before_model(tmp_path, state):
    participant = PausedPreparation(state)
    async with composition(
        tmp_path, DemoTool(), model=ScriptedModel([final()]), prepare=participant
    ) as (client, _app, model):
        session = await client.create()
        handle = await client.send(session.id, "Fabricated request", request_id="send")
        await participant.entered.wait()
        duplicate = await client.send(session.id, "Fabricated request", request_id="send")
        assert duplicate.turn_id == handle.turn_id
        assert not model.requests
        assert participant.calls == 1
        participant.release.set()
        result = await handle.result()
        assert result.stop_reason == ("final" if state == "ready" else "preparation_" + state)
        assert bool(model.requests) == (state == "ready")


@pytest.mark.parametrize("foreign", [False, True])
async def test_derived_preparation_reference_is_verified_without_replacing_accepted_input(
    tmp_path, foreign
):
    from tether.types import Message
    from tether.types.execution import Actor
    from tether.types.history import HistoryProvider, HistoryRecord

    class DerivedPreparation:
        history: HistoryProvider | None = None

        async def prepare(self, request):
            assert self.history is not None
            actor = Actor(id="foreign") if foreign else request.actor
            ref = await self.history.put(
                actor=actor,
                session_id=request.session_id,
                key="derived",
                record=HistoryRecord(
                    kind="message",
                    message=Message(role="user", content="Fabricated derived reference"),
                ),
            )
            return PreparationResult(state="ready", reason="ready", references=(ref,))

    participant = DerivedPreparation()
    async with composition(
        tmp_path, DemoTool(), model=ScriptedModel([final()]), prepare=participant
    ) as (client, app, model):
        participant.history = app.history
        session = await client.create()
        result = await (await client.send(session.id, "Fabricated original input")).result()
        assert result.stop_reason == ("preparation_refused" if foreign else "final")
        assert bool(model.requests) is not foreign
        if not foreign:
            assert any(
                message.content == "Fabricated original input"
                for message in model.requests[0].messages
            )
            assert all(
                message.content != "Fabricated derived reference"
                for message in model.requests[0].messages
            )
