"""A durable decision survives process death on either side of independent effect acceptance."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from consumer_independent_process import IndependentSQL
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_postgres_processes import force_expiry, receive, stop

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.commands import ReconcileSettlement
from tether.types.execution import AgentSpec
from tether.types.settlement import SettlementBinding
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("decision", ["commit", "rollback"])
@pytest.mark.parametrize("mode", ["decision", "effect"])
async def test_reconcile_reuses_durable_decision_after_independent_peer_death(
    database, postgres_server, mode, decision
):
    h = database
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL(
                "CREATE TABLE {}.application_effects (id text primary key, binding text)"
            ).format(sql.Identifier(h.schema))
        )
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("consumer_independent_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.write(
        (
            json.dumps(
                {
                    "connection": postgres_server.get_uri(),
                    "schema": h.schema,
                    "owner": h.actor.id,
                    "session": session.id,
                    "mode": mode,
                    "decision": decision,
                }
            )
            + "\n"
        ).encode()
    )
    await process.stdin.drain()
    try:
        binding = SettlementBinding.model_validate(await receive(process))
        assert binding.decision == decision
        assert (await store.effects(session.id, h.actor.id)).items
        entries = (await store.entries(session.id, h.actor.id, start=0, limit=100)).items
        assert any(e.kind == "settlement" and e.facts["status"] == "pending" for e in entries)
        assert not any(e.key and e.key.startswith("result:") for e in entries)
    finally:
        await stop(process)
    assert process.returncode is not None
    assert process.returncode < 0
    await force_expiry(h, session)
    engine = Engine(
        model=ScriptedModel([]),
        history=MessageHistoryAdapter(h.history),
        history_provider=h.history,
        settlements=(IndependentSQL(h.root),),
        **fabricated_context(),
    )
    spec = AgentSpec(id="fixture", version="1", title="Fixture", instructions="", model="fixture")
    try:
        async with store.turn(session.id, h.actor.id, turn_id="reconcile") as log:
            result = await engine.commands.invoke(
                h.actor,
                log,
                ReconcileSettlement(request_id="reconcile", settlement_id=binding.settlement_id),
                spec=spec,
            )
            assert result.stop_reason == "final"
        assert not (await store.effects(session.id, h.actor.id)).items
        terminal = "committed" if decision == "commit" else "rolled_back"
        outcome = await store.lookup(
            session.id,
            h.actor.id,
            kind="key",
            value="settlement:" + binding.settlement_id + ":" + terminal,
        )
        assert len(outcome) == 1
        assert outcome[0].facts["binding"] == binding.model_dump(mode="json")
        async with h.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT count(*) FROM {}.application_effects").format(
                    sql.Identifier(h.schema)
                )
            )
            assert await cursor.fetchone() == (1,)
    finally:
        await engine.aclose()
