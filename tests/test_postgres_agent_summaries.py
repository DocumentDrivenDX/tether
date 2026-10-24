"""Selected SQL journal and content pair shares accepted summary lookup and reuse."""

from types import SimpleNamespace

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_agent_summaries import Strategy, configuration, response, turn

from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.postgres import PostgresSessionStore
from tether.types.execution import AgentSpec
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_sql_summary_pair_preserves_provenance_usage_and_reuse(limits, database):
    actor = database.actor
    store = PostgresSessionStore(database.root).for_actor(actor)
    session = await store.create(actor.id, SessionInit())
    model = ScriptedModel(
        [response("Fabricated old answer"), response("SQL summary", 7), response(), response()]
    )
    engine = Engine(
        **configuration(),
        context=Strategy(),
        model=model,
        history=MessageHistoryAdapter(database.history),
        history_provider=database.history,
        model_limits=limits,
    )
    h = SimpleNamespace(
        actor=actor,
        store=store,
        session=session,
        engine=engine,
        model=model,
        spec=AgentSpec(
            id="demo", version="1", title="Demo", instructions="Fabricated", model="demo"
        ),
    )
    try:
        await turn(h, "first")
        assert (await turn(h, "second"))[0].stop_reason == "final"
        assert (await turn(h, "third"))[0].stop_reason == "final"
        assert [r.tags.purpose for r in model.requests] == ["answer", "summary", "answer", "answer"]
        group = await store.lookup(
            session.id, actor.id, kind="model_call", value=model.requests[1].tags.model_call_id
        )
        assert {e.kind for e in group} == {
            "compaction",
            "model_call",
            "model_attempt",
            "model_receipt",
        }
        summary = (await store.snapshot(session.id, actor.id)).view.summary
        assert summary.usage.input_tokens == 13
        assert summary.usage_complete
        assert "SQL summary" in repr(model.requests[-1].messages)
    finally:
        assert await engine.aclose()
