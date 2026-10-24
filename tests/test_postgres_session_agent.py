"""Actual Pydantic AI Agent/Client with SQL journal and independent SQL history roots."""

from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql
from test_gated_execution import DemoTool, final, proposal
from test_postgres_agent import engine

from tether import Tether
from tether.models.testing import ScriptedModel
from tether.stores.archive import InMemoryArchive
from tether.stores.postgres import DatabaseRoot, PostgresHistory, migrate
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.archive import ArchiveLimits
from tether.types.execution import Actor, AgentSpec, Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def spec(tool):
    return AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
    )


async def test_sql_agent_approval_restarts_then_archives_and_restores_without_execution(
    database, limits, client_limits
):
    h = database
    tool = DemoTool()
    selected = spec(tool)
    maintenance = Actor(id="maintenance")
    store = PostgresSessionStore(h.root, maintenance=maintenance)
    history_root = await DatabaseRoot.open(h.source, actor=maintenance, schema=h.schema)
    history = PostgresHistory(history_root)
    first = engine(history, limits, tool, selected, [proposal()])
    archive = InMemoryArchive(ArchiveLimits(max_sessions=4, max_entries=1000, max_bytes=1048576))
    session, invocation, original = await accept_pending(
        h, first, store, history, history_root, client_limits, tool
    )
    root = await DatabaseRoot.open(h.source, actor=maintenance, schema=h.schema)
    history_root = await DatabaseRoot.open(h.source, actor=maintenance, schema=h.schema)
    history = PostgresHistory(history_root)
    second = engine(history, limits, tool, selected, [final()])
    try:
        async with Tether(
            engine=second,
            store=PostgresSessionStore(root, maintenance=maintenance),
            history=history,
            limits=client_limits,
            archive=archive,
            archive_sink=archive,
        ) as app:
            client = app.client(h.actor)
            pending = await client.pending(session.id)
            assert pending is not None
            assert pending.invocation_id == invocation
            handle = await client.confirm(session.id, invocation, Answer(approved=True))
            assert handle is not None
            assert (await handle.result()).stop_reason == "final"
            assert len(tool.calls) == 1
            assert await client.confirm(session.id, invocation, Answer(approved=True)) is None
            entries = (await client.entries(session.id)).items
            assert entries[: len(original)] == original
            assert all(
                slot.value.status == "ready" for slot in (await client.history(session.id)).items
            )
            call_ids = tuple(
                str(e.facts["model_call_id"]) for e in entries if e.kind == "model_call"
            )
            assert all(
                detail.status == "ready"
                for detail in await client.call_detail(session.id, call_ids)
            )
            await client.update(session.id, tags=("archived",))
            await app.archive_session(h.actor, session.id)
            assert (await client.get(session.id, tier="archive")).summary.tier == "archive"
    finally:
        assert await root.aclose()
        assert await history_root.aclose()
    await restore_archive(h, maintenance, archive, tool, selected, session, limits, client_limits)


async def restore_archive(h, maintenance, archive, tool, selected, session, limits, client_limits):
    target_schema = "restore_" + uuid4().hex
    await migrate(h.source, actor=maintenance, schema=target_schema)
    target = await DatabaseRoot.open(h.source, actor=maintenance, schema=target_schema)
    target_history = PostgresHistory(target)
    reader = engine(target_history, limits, tool, selected, [])
    try:
        async with Tether(
            engine=reader,
            store=PostgresSessionStore(target, maintenance=maintenance),
            history=target_history,
            limits=client_limits,
            archive=archive,
        ) as app:
            client = app.client(h.actor)
            assert not (await client.list()).items
            restored = await client.restore(session.id)
            assert restored.id == session.id
            assert await client.restore(session.id) == restored
            assert (await client.get(session.id)).summary.tier == "live"
            assert all(
                slot.value.status == "ready" for slot in (await client.history(session.id)).items
            )
            assert isinstance(reader.model, ScriptedModel)
            assert not reader.model.requests
            assert len(tool.calls) == 1
    finally:
        assert await target.aclose()
        async with h.pool.connection() as connection:
            await connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target_schema))
            )


async def accept_pending(h, first, store, history, history_root, client_limits, tool):
    try:
        async with Tether(engine=first, store=store, history=history, limits=client_limits) as app:
            client = app.client(h.actor)
            session = await client.create(
                spec="demo", title="Fabricated", settings={"approval": "manual"}
            )
            waiting = await (
                await client.send(session.id, "Fabricated input", request_id="original")
            ).result()
            assert waiting.stop_reason == "confirmation_required"
            invocation = waiting.pending[0].invocation_id
            original = (await client.entries(session.id)).items
            assert not tool.calls
            return session, invocation, original
    finally:
        assert await history_root.aclose()
