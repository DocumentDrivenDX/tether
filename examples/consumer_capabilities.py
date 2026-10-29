"""Fabricated public-Client integration; no model service, credentials or paid calls.

Run: uv run --locked python examples/consumer_capabilities.py
"""
from __future__ import annotations
import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, cast
from pydantic import BaseModel
from tether import Client, Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.testing.approval_edits import NarrowCount
from tether.testing.conformance.types import require
from tether.testing.preparation import ReadyPreparation
from tether.testing.settlement import MemorySettlement
from tether.types import JsonValue, ModelResponse, ToolCall
from tether.types.auxiliary import AuxiliaryProfile
from tether.types.execution import Actor, AgentSpec, Answer, SettlementStage, ToolContext, ToolOutput

class CountArgs(BaseModel):
    count: int
    label: str = 'fabricated-default'

class EmptyArgs(BaseModel):
    pass

class FabricatedWrite:
    (name, version, description) = ('write', '1', 'Stage a fabricated count.')
    input_model: type[BaseModel] = CountArgs
    requires: frozenset[str] = frozenset()
    (model_visible, always_ask) = (True, True)
    effect: Literal['read_only', 'effectful'] = 'effectful'
    replay: Literal['safe', 'unsafe'] = 'unsafe'
    timeout_seconds: float | None = None
    definition_hash: str | None = None
    traits: dict[str, JsonValue]

    def __init__(self) -> None:
        self.counts: list[int] = []
        self.traits = {}

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        self.counts.append(cast(CountArgs, args).count)
        return ToolOutput(text='Fabricated staged count accepted.', settlement=SettlementStage(participant='fabricated', stage_id=ctx.invocation_id))

class FabricatedStatus(FabricatedWrite):
    (name, description) = ('status', 'Read fabricated status.')
    input_model: type[BaseModel] = EmptyArgs
    always_ask = False
    effect: Literal['read_only', 'effectful'] = 'read_only'
    replay: Literal['safe', 'unsafe'] = 'safe'

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return False

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        return ToolOutput(text='Fabricated status: ready.')

class FabricatedCredential:

    async def authorization(self) -> str:
        return ' '.join(('Bearer', 'fabricated'))

async def answer_with_edit(
    client: Client, session_id: str, tool: FabricatedWrite, participant: MemorySettlement,
    preparation: ReadyPreparation, report: Callable[[str], Awaitable[None]],
) -> None:
    first = await client.send(session_id, 'Fabricated count request', request_id='answer')
    duplicate = await client.send(session_id, 'Fabricated count request', request_id='answer')
    require(first.turn_id == duplicate.turn_id, 'consumer_example_invariant')
    pending = (await first.result()).pending[0]
    require(len(preparation.requests) == 1, 'consumer_example_invariant')
    require(not tool.counts, 'consumer_example_invariant')
    await report('Preparation accepted once. Proposal: count 3. Waiting for approval; no write.')
    handle = await client.confirm(session_id, pending.invocation_id, Answer(approved=True, arguments={'count': 1}))
    require(handle is not None, 'consumer_example_invariant')
    require((await handle.result()).stop_reason == 'final', 'consumer_example_invariant')
    require(tool.counts == [1], 'consumer_example_invariant')
    require(participant.calls == 1, 'consumer_example_invariant')
    require(not (await client.effects(session_id)).items, 'consumer_example_invariant')
    await report('Edited approval: count 1. Original/shown/executable bindings retained. Settlement committed.')


async def exercise(root: Path, report: Callable[[str], Awaitable[None]]) -> dict[str, JsonValue]:
    files = await JsonlRoot.open(root)
    (tool, status) = (FabricatedWrite(), FabricatedStatus())
    (participant, preparation) = (MemorySettlement(), ReadyPreparation())
    initial = AgentSpec(id='initial', version='1', title='Fabricated initial', instructions='Fabricated', model='fixture', tools=(tool.name, status.name))
    next_spec = initial.model_copy(update={'id': 'next', 'version': '2', 'tools': (status.name,)})
    model = ScriptedModel([ModelResponse(finish_reason='tool_calls', tool_calls=(ToolCall(id='fabricated-call', name='write', arguments={'count': 3}),)), ModelResponse(finish_reason='stop', content='Fabricated answer complete.'), ModelResponse(finish_reason='stop', content='Fabricated suggestion.')])
    history = await JsonlHistory.open(files)
    engine = Engine(model=model, history=MessageHistoryAdapter(history), history_provider=history, tools=(tool, status), specs=(initial, next_spec), prepare=preparation, settlements=(participant,), approval_transforms={'write': NarrowCount()}, auxiliary_profiles=(AuxiliaryProfile(id='suggestion', revision='1', model='fixture', instructions='Suggest a fabricated label.'),), **fabricated_context())
    actor = Actor(id='fabricated-owner', credential=FabricatedCredential())
    try:
        async with Tether(engine=engine, store=await JsonlSessionStore.open(files), history=history, default_spec=initial.id) as app:
            client = app.client(actor)
            session = await client.create()
            await answer_with_edit(client, session.id, tool, participant, preparation, report)
            generation = engine.tool_registry.publish((status,))
            await (await client.transition_spec(session.id, next_spec.id, request_id='transition')).result()
            direct = await client.invoke_tool(session.id, status.name, {}, request_id='status')
            require((await direct.result()).messages[0].content == 'Fabricated status: ready.', 'consumer_example_invariant')
            require(len(model.requests) == 2, 'consumer_example_invariant')
            await report('Published immutable registry. Transitioned to next specification. Direct status call used no model.')
            snapshot = await client.snapshot(session.id)
            auxiliary = await client.auxiliary(session.id, 'suggestion', through_index=snapshot.through_index, request_id='suggestion')
            result = await auxiliary.result()
            require(result.messages[0].content == 'Fabricated suggestion.', 'consumer_example_invariant')
            require(model.requests[-1].tools == (), 'consumer_example_invariant')
            require(model.requests[-1].tags.purpose == 'auxiliary', 'consumer_example_invariant')
            await report('Caller-bound suggestion accepted with no tools. Three model calls total; one staged write.')
            return {'model_calls': len(model.requests), 'staged_writes': participant.calls, 'preparations': len(preparation.requests), 'registry_generation': generation, 'source_profile': 'fabricated; volatile application participant; durable JSONL journal/history'}
    finally:
        await files.aclose()

async def main() -> None:

    async def report(text: str) -> None:
        print(text)
    with tempfile.TemporaryDirectory(prefix='tether-consumer-example-') as directory:
        await exercise(Path(directory), report)
if __name__ == '__main__':
    asyncio.run(main())
