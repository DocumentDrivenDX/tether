"""Actual SQL Client observations preserve receipt positions and caller identity."""

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_client_resume import FreshCredential
from test_telemetry_ports import id_factory

from tether import Tether
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.postgres import PostgresSessionStore
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.execution import Actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_sql_client_observations_follow_accepted_metadata_and_current_caller(
    database, limits, client_limits
):
    h = database
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    engine = Engine(
        model=ScriptedModel([]),
        history=MessageHistoryAdapter(h.history),
        history_provider=h.history,
        model_limits=limits,
        audit=sink,
        tracer=tracer,
    )
    store = PostgresSessionStore(h.root)
    credential = FreshCredential()
    actor = Actor(id=h.actor.id, principal="current", credential=credential)
    app = Tether(engine=engine, store=store, history=h.history, limits=client_limits, resume="off")
    async with app:
        h.calls.clear()
        client = app.client(actor)
        made = await client.create(ref="fabricated", title="title content marker")
        await client.update(made.id, title="updated content marker", tags=["tag content marker"])
        await client.snapshot(made.id)
        rows = (await store.for_actor(actor).entries(made.id, actor.id, start=0, limit=3)).items
        assert h.calls
        assert all(
            caller.credential is credential and caller.principal == "current"
            for caller, _ in h.calls
        )
    semantic = [r for r in sink.records if r.kind in {"session_started", "session_settings"}]
    assert len(semantic) == 3
    assert [r.detail["entry_position"] for r in semantic if r.kind == "session_settings"] == [
        e.index for e in rows
    ]
    assert all(
        r.context.actor == actor.id and r.context.principal == "current" for r in sink.records
    )
    assert "content marker" not in repr(sink.records) + repr(tracer.records)
