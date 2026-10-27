"""Child Engine that pauses after a fabricated PostgreSQL-backed tool effect."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from jsonl_effect_process import EffectTool, emit
from psycopg import AsyncConnection
from test_resume_engine import build

from tether.stores.postgres import DatabaseRoot, PostgresHistory
from tether.stores.postgres.store import PostgresSessionStore
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor
from tether.types.storage import SessionInit, StorageLimits


async def main() -> None:
    config = json.loads(await asyncio.to_thread(sys.stdin.readline))
    actor = Actor(id=config["owner"])

    @asynccontextmanager
    async def source(caller, session_id):
        assert caller.id == actor.id
        async with await AsyncConnection.connect(config["connection"]) as connection:
            yield connection

    root = await DatabaseRoot.open(
        source, actor=actor, schema=config["schema"], limits=StorageLimits(lease_seconds=5)
    )
    store = PostgresSessionStore(root).for_actor(actor)
    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )
    h = SimpleNamespace(
        store=store,
        provider=PostgresHistory(root),
        actor=actor,
        engines=[],
        limits=limits,
    )
    run = build(h, tool=EffectTool(Path(config["effect_path"])))
    session = await store.create(actor.id, SessionInit(settings={"approval": "auto"}))
    emit({"event": "created", "session_id": session.id})
    async with store.turn(session.id, actor.id, turn_id="killed-turn") as log:
        await run.engine.run_turn(actor, log, "Fabricated request", spec=run.spec)


if __name__ == "__main__":
    asyncio.run(main())
