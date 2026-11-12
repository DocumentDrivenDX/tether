"""Independent-process death tests the shared transaction's actual visibility frontiers."""

import asyncio
import sys
from pathlib import Path

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from process_lifetime_controls import OwnedProcess, RetainedCleanup
from psycopg import sql
from test_postgres_processes import receive

from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import HistoryRef
from tether.types.settlement import SettlementBinding
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def create_application_stage(h, decision):
    async with h.pool.connection() as connection:
        await connection.execute(
            sql.SQL("CREATE TABLE {}.application_effects (id text primary key)").format(
                sql.Identifier(h.schema)
            )
        )
        if decision == "rollback":
            await connection.execute(
                sql.SQL("INSERT INTO {}.application_effects VALUES (%s)").format(
                    sql.Identifier(h.schema)
                ),
                ("fabricated-settlement",),
            )


@pytest.mark.parametrize("decision", ["commit", "rollback"])
@pytest.mark.parametrize("mode", ["staged", "participant", "before_commit", "after_commit"])
async def test_killed_peer_never_publishes_partial_history_application_journal_or_feed(
    database, postgres_server, mode, decision
):
    h = database
    await create_application_stage(h, decision)
    store = PostgresSessionStore(h.root).for_actor(h.actor)
    session = await store.create(h.actor.id, SessionInit())
    before = await store.watermark(h.actor.id)
    process: OwnedProcess | None = None
    ref: HistoryRef | None = None
    committed = mode == "after_commit"
    async with RetainedCleanup() as child_cleanup:
        process = await OwnedProcess.spawn(
            sys.executable,
            "-u",
            str(Path(__file__).with_name("consumer_transaction_process.py")),
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
        staged = await receive(process)
        assert staged["event"] == "staged"
        ref = HistoryRef.model_validate(staged["ref"])
        assert (await receive(process))["event"] == "frontier"
        assert (await h.history.read([ref], actor=h.actor))[0].status == (
            "ready" if committed else "missing"
        )
        async with h.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT count(*) FROM {}.application_effects").format(
                    sql.Identifier(h.schema)
                )
            )
            expected_rows = int(committed) if decision == "commit" else int(not committed)
            assert await cursor.fetchone() == (expected_rows,)
    assert process is not None
    assert ref is not None
    assert process.returncode is not None
    assert process.returncode < 0
    view = await store.get(session.id, h.actor.id)
    assert view is not None
    assert view.summary.message_count == int(committed)
    assert view.summary.entry_count == (4 if committed else 0)
    assert await store.watermark(h.actor.id) == before + int(committed)
    terminal = "committed" if decision == "commit" else "rolled_back"
    outcome = await store.lookup(
        session.id,
        h.actor.id,
        kind="key",
        value="settlement:fabricated-settlement:" + terminal,
    )
    assert bool(outcome) is committed
    if committed:
        assert SettlementBinding.model_validate(outcome[0].facts["binding"]).decision == decision
    known = await store.lookup(session.id, h.actor.id, kind="key", value="atomic-message")
    assert bool(known) is committed
    assert (await h.history.read([ref], actor=h.actor))[0].status == (
        "ready" if committed else "missing"
    )
