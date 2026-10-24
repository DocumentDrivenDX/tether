"""Actual Agent approval reconstruction with independent JSONL journal/Postgres content."""

import asyncio

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_client_resume import FreshCredential
from test_gated_execution import DemoTool, final, proposal

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.postgres import DatabaseRoot, PostgresHistory
from tether.types.execution import Actor, AgentSpec, Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def engine(provider, limits, tool, spec, responses):
    return Engine(
        **fabricated_context(),
        model=ScriptedModel(responses),
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        model_limits=limits,
        tools=(tool,),
        specs=(spec,),
    )


async def test_pending_approval_reconstructs_after_both_roots_reopen(
    database,
    tmp_path,
    limits,
    client_limits,
):
    h = database
    tool = DemoTool()
    spec = AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
    )
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    first = engine(h.history, limits, tool, spec, [proposal()])
    async with Tether(engine=first, store=store, history=h.history, limits=client_limits) as app:
        client = app.client(h.actor)
        session = await client.create(spec="demo", settings={"approval": "manual"})
        waiting = await (await client.send(session.id, "Fabricated input")).result()
        assert waiting.stop_reason == "confirmation_required"
        invocation = waiting.pending[0].invocation_id
        assert not tool.calls
    assert await root.aclose()
    assert await h.root.aclose()
    database_root = await DatabaseRoot.open(h.source, actor=h.actor, schema=h.schema)
    history = PostgresHistory(database_root)
    root = await JsonlRoot.open(tmp_path)
    store = await JsonlSessionStore.open(root)
    second = engine(history, limits, tool, spec, [final()])
    try:
        async with Tether(engine=second, store=store, history=history, limits=client_limits) as app:
            client = app.client(h.actor)
            pending = await client.pending(session.id)
            assert pending is not None
            assert pending.invocation_id == invocation
            handle = await client.confirm(session.id, invocation, Answer(approved=True))
            assert handle is not None
            result = await handle.result()
            assert result.stop_reason == "final"
            assert len(tool.calls) == 1
            assert all(
                slot.value.status == "ready" for slot in (await client.history(session.id)).items
            )
            entries = (await client.entries(session.id)).items
            calls = tuple(str(e.facts["model_call_id"]) for e in entries if e.kind == "model_call")
            assert len(calls) == 2
            assert all(
                detail.status == "ready" for detail in await client.call_detail(session.id, calls)
            )
    finally:
        assert await root.aclose()
        assert await database_root.aclose()


async def test_current_same_owner_credentials_are_not_cached_or_replaced(database):
    h = database
    credentials = [FreshCredential(), FreshCredential()]
    actors = [
        Actor(id=h.actor.id, principal=principal, credential=credential)
        for principal, credential in zip(["first-model", "second-model"], credentials, strict=True)
    ]
    h.calls.clear()
    await asyncio.gather(
        *(h.root.run(actor, "session", lambda c: c.execute("SELECT 1")) for actor in actors)
    )
    assert {actor.principal for actor, _ in h.calls} == {"first-model", "second-model"}
    assert {id(actor.credential) for actor, _ in h.calls} == {id(c) for c in credentials}
    assert all(actor.id == h.actor.id for actor, _ in h.calls)
    assert all(session == "session" for _, session in h.calls)
