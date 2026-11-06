"""Actual PostgreSQL observers verify shared application/history/journal publication."""

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_consumer_settlement import StagedTool, Withhold
from test_gated_execution import final

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.postgres.settlement import PostgresSettlementParticipant
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.commands import InvokeTool
from tether.types.execution import AgentSpec
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("mode", ["commit", "rollback", "fail"])
async def test_shared_participant_and_result_publish_together_or_both_roll_back(database, mode):
    h = database
    table = sql.Identifier(h.schema, "application_effects")
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("CREATE TABLE {} (id text primary key, decision text)").format(table)
        )
    observations = []

    async def apply(transaction, binding, actor):
        assert actor.id == h.actor.id
        await transaction.execute("write", [binding.settlement_id, binding.decision])
        async with h.pool.connection() as connection:
            cursor = await connection.execute(sql.SQL("SELECT count(*) FROM {}").format(table))
            observations.append(await cursor.fetchone())
        if mode == "fail":
            raise RuntimeError("fabricated participant failure")

    participant = PostgresSettlementParticipant(
        h.root,
        id="fabricated",
        operations={"write": sql.SQL("INSERT INTO {} VALUES (%s,%s)").format(table).as_string()},
        apply=apply,
    )
    tool = StagedTool()
    spec = AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="",
        model="fixture",
        tools=(tool.name,),
    )
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    engine = Engine(
        model=ScriptedModel([final()]),
        history=MessageHistoryAdapter(h.history),
        history_provider=h.history,
        tools=(tool,),
        settlements=(participant,),
        guards=(Withhold(),) if mode == "rollback" else (),
        **fabricated_context(),
    )
    try:
        async with store.turn(session.id, h.actor.id, turn_id="operation") as log:
            command = InvokeTool(request_id="command", tool_name="write", arguments={"count": 1})
            if mode == "fail":
                failed = await engine.commands.invoke(h.actor, log, command, spec=spec)
                assert failed.stop_reason == "tool_denied"
                assert failed.reason == "settlement_failed"
                assert not any(
                    e.kind == "settlement" or (e.key and e.key.startswith("result:"))
                    for e in log.entries
                )
            else:
                result = await engine.commands.invoke(h.actor, log, command, spec=spec)
                assert result.messages[0].is_error is (mode == "rollback")
                assert any(
                    e.kind == "settlement"
                    and e.facts["status"] == ("rolled_back" if mode == "rollback" else "committed")
                    for e in log.entries
                )
            async with h.pool.connection() as connection:
                cursor = await connection.execute(sql.SQL("SELECT decision FROM {}").format(table))
                assert await cursor.fetchall() == ([] if mode == "fail" else [(mode,)])
            assert observations == [(0,)]
            effects = await store.effects(session.id, h.actor.id)
            assert bool(effects.items) is (mode == "fail")
    finally:
        await engine.aclose()
