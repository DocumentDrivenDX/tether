"""A durable decision survives process death on either side of independent effect acceptance."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from consumer_independent_process import IndependentSQL
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from process_lifetime_controls import OwnedProcess, RetainedCleanup, unarmed_timeouts
from psycopg import sql
from test_postgres_processes import force_expiry, receive

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
    process: OwnedProcess | None = None
    binding: SettlementBinding | None = None
    async with RetainedCleanup() as child_cleanup:
        process = await OwnedProcess.spawn(
            sys.executable,
            "-u",
            str(Path(__file__).with_name("consumer_independent_process.py")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        child_cleanup.add(process.close)
        await process.send_json(
            {
                "connection": postgres_server.get_uri(),
                "schema": h.schema,
                "owner": h.actor.id,
                "session": session.id,
                "mode": mode,
                "decision": decision,
            }
        )
        binding = SettlementBinding.model_validate(await receive(process))
        assert binding.decision == decision
        assert (await store.effects(session.id, h.actor.id)).items
        entries = (await store.entries(session.id, h.actor.id, start=0, limit=100)).items
        assert any(e.kind == "settlement" and e.facts["status"] == "pending" for e in entries)
        assert not any(e.key and e.key.startswith("result:") for e in entries)
    assert process is not None
    assert binding is not None
    assert process.returncode is not None
    assert process.returncode < 0
    await force_expiry(h, session)
    spec = AgentSpec(id="fixture", version="1", title="Fixture", instructions="", model="fixture")
    async with RetainedCleanup() as engine_cleanup:
        with unarmed_timeouts(
            importlib.import_module("tether.engine.settlement")
        ) as timeout_values:
            engine = Engine(
                model=ScriptedModel([]),
                history=MessageHistoryAdapter(h.history),
                history_provider=h.history,
                settlements=(IndependentSQL(h.root, timeout_values=timeout_values),),
                **fabricated_context(),
            )
            engine_cleanup.add(
                engine.aclose,
                engine.wait_closed,
                engine.wait_observers_closed,
            )
            async with store.turn(session.id, h.actor.id, turn_id="reconcile") as log:
                result = await engine.commands.invoke(
                    h.actor,
                    log,
                    ReconcileSettlement(
                        request_id="reconcile", settlement_id=binding.settlement_id
                    ),
                    spec=spec,
                )
            assert timeout_values
            assert all(value == engine.limits.hook_timeout_seconds for value in timeout_values)
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
