"""Separate processes and actual client kill qualify SQL takeover and stale operations."""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from process_lifetime_controls import OwnedProcess, RetainedCleanup
from process_lifetime_controls import receive as receive
from process_lifetime_controls import stop as stop
from psycopg import sql

from tether.stores.postgres.capture import Capture
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import Actor, NewEntry
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def peer(
    h,
    server,
    session,
    turn,
    cleanup: RetainedCleanup,
    *,
    unsafe=False,
    effect_path=None,
    lease_seconds=5,
) -> OwnedProcess:
    process = await OwnedProcess.spawn(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("postgres_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    cleanup.add(process.close)
    await process.send_json(
        {
            "connection": server.get_uri(),
            "schema": h.schema,
            "owner": h.actor.id,
            "session": session.id,
            "turn": turn,
            "unsafe": unsafe,
            "effect_path": str(effect_path) if effect_path is not None else None,
            "lease_seconds": lease_seconds,
        }
    )
    return process


async def expired(h, session):
    while True:
        async with h.pool.connection() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT lease_until<=clock_timestamp() FROM {}.sessions "
                    "WHERE owner=%s AND id=%s"
                ).format(sql.Identifier(h.schema)),
                (h.actor.id.encode(), session.id.encode()),
            )
            if await cursor.fetchone() == (True,):
                return
        await asyncio.sleep(0.02)


async def force_expiry(h, session):
    """Expire a live peer's lease at a controlled point in the takeover test."""
    async with h.pool.connection() as connection:
        cursor = await connection.execute(
            sql.SQL(
                "UPDATE {}.sessions SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE owner=%s AND id=%s AND lease_until>clock_timestamp()"
            ).format(sql.Identifier(h.schema)),
            (h.actor.id.encode(), session.id.encode()),
        )
        assert cursor.rowcount == 1, "the original lease must still be live"


async def test_separate_process_exclusivity_takeover_stale_writes_and_cleanup(
    database, postgres_server
):
    h = database
    session = await Capture(h.root, h.actor).create(SessionInit(), str(uuid4()))
    async with RetainedCleanup() as children:
        original = await peer(h, postgres_server, session, "old-phase", children, lease_seconds=120)
        assert (await original.event())["event"] == "acquired"
        blocked = await peer(h, postgres_server, session, "blocked", children)
        assert await blocked.event() == {"event": "refused", "error": "TurnInProgress"}
        assert await blocked.wait() == 0
        await force_expiry(h, session)
        assert original.returncode is None
        await expired(h, session)
        assert original.returncode is None
        replacement = await peer(h, postgres_server, session, "new-phase", children)
        assert (await replacement.event())["event"] == "acquired"
        assert (await original.command("append"))["error"] == "LeaseLost"
        assert (await original.command("heartbeat"))["error"] == "LeaseLost"
        assert (await original.command("release"))["event"] == "ok"
        assert (await replacement.command("append"))["event"] == "ok"
        assert (await replacement.command("heartbeat"))["event"] == "ok"
        scoped = PostgresSessionStore(h.root).for_actor(h.actor)
        entries = (await scoped.entries(session.id, h.actor.id, start=0, limit=20)).items
        ended = [e for e in entries if e.kind == "turn_ended"]
        assert [(e.turn_id, e.facts["stop_reason"]) for e in ended] == [
            ("old-phase", "interrupted")
        ]
        assert sum(e.key == "child-append" for e in entries) == 1


async def test_actual_client_kill_preserves_committed_intent_and_recovery_barrier(
    database, postgres_server, tmp_path
):
    h = database
    session = await Capture(h.root, h.actor).create(SessionInit(), str(uuid4()))
    effect_path = tmp_path / "fabricated-effects.log"
    async with RetainedCleanup() as children:
        process = await peer(
            h,
            postgres_server,
            session,
            "killed-phase",
            children,
            unsafe=True,
            effect_path=effect_path,
        )
        assert (await process.event())["entries"] == 2
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        await process.close()
        assert process.returncode is not None
        assert process.returncode < 0
        await expired(h, session)
        root_store = PostgresSessionStore(h.root, maintenance=Actor(id="maintenance"))
        recovered = await root_store.recover(now=datetime.now(UTC))
        assert [(r.session_id, r.turn_id) for r in recovered] == [(session.id, "killed-phase")]
        assert await root_store.recover(now=datetime.now(UTC)) == ()
        scoped = root_store.for_actor(h.actor)
        view = await scoped.get(session.id, h.actor.id)
        assert view is not None
        assert view.summary.status == "interrupted"
        assert view.summary.unresolved_effect_count == 1
        async with scoped.turn(session.id, h.actor.id, turn_id="replacement") as log:
            assert any(e.key == "intent:fabricated" for e in log.entries)
            await log.append([NewEntry(kind="message", key="replacement-metadata")])
        assert (await scoped.effects(session.id, h.actor.id)).items[0].invocation_id == "fabricated"
        assert effect_path.read_bytes() == b"fabricated-effect\n"
