"""Fabricated subprocess peer using only injected SQL connection acquisition."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection

from tether.stores.postgres import DatabaseRoot
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.execution import Actor, NewEntry
from tether.types.storage import StorageLimits


def emit(value):
    print(json.dumps(value), flush=True)


def record_effect(path):
    with Path(path).open("ab") as target:
        target.write(b"fabricated-effect\n")
        target.flush()
        os.fsync(target.fileno())


async def commands(log):
    while True:
        raw = await asyncio.to_thread(sys.stdin.readline)
        if not raw:
            return
        command = json.loads(raw)["command"]
        if command == "quit":
            return
        try:
            if command == "append":
                await log.append([NewEntry(kind="message", key="child-append")])
            elif command == "heartbeat":
                await log.heartbeat()
            elif command == "release":
                await log.aclose()
            else:
                raise ValueError("unknown_command")
            emit({"event": "ok", "command": command})
        except Exception as error:
            emit({"event": "refused", "command": command, "error": type(error).__name__})


async def run(config):
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
    try:
        async with store.turn(config["session"], actor.id, turn_id=config["turn"]) as log:
            await log.append(
                [
                    NewEntry(
                        kind="turn_started",
                        turn_id=config["turn"],
                        facts={"spec_id": "demo", "spec_version": "1", "spec_digest": "digest"},
                    )
                ]
            )
            if config.get("unsafe"):
                await log.append(
                    [
                        NewEntry(
                            kind="tool_call",
                            key="intent:fabricated",
                            turn_id=config["turn"],
                            facts={
                                "invocation_id": "fabricated",
                                "tool": "fabricated-tool",
                                "effect": "effectful",
                                "status": "started",
                            },
                        )
                    ]
                )
                if config.get("effect_path"):
                    await asyncio.to_thread(record_effect, config["effect_path"])
            emit({"event": "acquired", "turn": log.turn_id, "entries": len(log.entries)})
            await commands(log)
    except Exception as error:
        emit({"event": "refused", "error": type(error).__name__})
    finally:
        await root.aclose()


if __name__ == "__main__":
    asyncio.run(run(json.loads(sys.stdin.readline())))
