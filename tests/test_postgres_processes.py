"""Separate processes and actual client kill qualify SQL takeover and stale operations."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from psycopg import sql

from tether.stores.postgres.capture import Capture
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import Actor, NewEntry
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def peer(h, server, session, turn, *, unsafe=False, effect_path=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("postgres_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    process.stdin.write(
        (
            json.dumps(
                {
                    "connection": server.get_uri(),
                    "schema": h.schema,
                    "owner": h.actor.id,
                    "session": session.id,
                    "turn": turn,
                    "unsafe": unsafe,
                    "effect_path": str(effect_path) if effect_path is not None else None,
                }
            )
            + "\n"
        ).encode()
    )
    await process.stdin.drain()
    return process


async def receive(process):
    assert process.stdout is not None
    raw = await asyncio.wait_for(process.stdout.readline(), 15)
    assert raw, "child exited without a protocol response"
    return json.loads(raw)


async def command(process, value):
    assert process.stdin is not None
    process.stdin.write((json.dumps({"command": value}) + "\n").encode())
    await process.stdin.drain()
    return await receive(process)


async def stop(process):
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.communicate(), 10)


async def expired(h, session):
    async with asyncio.timeout(10):
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


async def test_separate_process_exclusivity_takeover_stale_writes_and_cleanup(
    database, postgres_server
):
    h = database
    session = await Capture(h.root, h.actor).create(SessionInit(), str(uuid4()))
    original = await peer(h, postgres_server, session, "old-phase")
    peers = [original]
    try:
        assert (await receive(original))["event"] == "acquired"
        blocked = await peer(h, postgres_server, session, "blocked")
        peers.append(blocked)
        assert await receive(blocked) == {"event": "refused", "error": "TurnInProgress"}
        await asyncio.wait_for(blocked.wait(), 10)
        await expired(h, session)
        replacement = await peer(h, postgres_server, session, "new-phase")
        peers.append(replacement)
        assert (await receive(replacement))["event"] == "acquired"
        assert (await command(original, "append"))["error"] == "LeaseLost"
        assert (await command(original, "heartbeat"))["error"] == "LeaseLost"
        assert (await command(original, "release"))["event"] == "ok"
        assert (await command(replacement, "append"))["event"] == "ok"
        assert (await command(replacement, "heartbeat"))["event"] == "ok"
        scoped = PostgresSessionStore(h.root).for_actor(h.actor)
        entries = (await scoped.entries(session.id, h.actor.id, start=0, limit=20)).items
        ended = [e for e in entries if e.kind == "turn_ended"]
        assert [(e.turn_id, e.facts["stop_reason"]) for e in ended] == [
            ("old-phase", "interrupted")
        ]
        assert sum(e.key == "child-append" for e in entries) == 1
    finally:
        await asyncio.gather(*(stop(process) for process in peers))


async def test_actual_client_kill_preserves_committed_intent_and_recovery_barrier(
    database, postgres_server, tmp_path
):
    h = database
    session = await Capture(h.root, h.actor).create(SessionInit(), str(uuid4()))
    effect_path = tmp_path / "fabricated-effects.log"
    process = await peer(
        h, postgres_server, session, "killed-phase", unsafe=True, effect_path=effect_path
    )
    try:
        assert (await receive(process))["entries"] == 2
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        await stop(process)
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
    finally:
        await stop(process)
