"""Fabricated crash peer; all database configuration arrives over private stdin."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from psycopg import AsyncConnection, sql

from tether.stores.postgres import DatabaseRoot, PostgresHistory
from tether.stores.postgres.settlement import PostgresSettlementParticipant
from tether.stores.postgres.store import PostgresSessionStore
from tether.types import Message
from tether.types.execution import Actor, NewEntry
from tether.types.history import HistoryRecord
from tether.types.settlement import SettlementBinding

T = TypeVar("T")


def emit(value):
    print(json.dumps(value), flush=True)


class FaultRoot(DatabaseRoot):
    armed = False
    mode = ""

    async def run(
        self,
        actor: Actor,
        session_id: str | None,
        operation: Callable[[AsyncConnection[tuple[object, ...]]], Awaitable[T]],
        *,
        write: bool = False,
    ) -> T:
        async def intercept(connection):
            result = await operation(connection)
            if self.armed and write and self.mode == "before_commit":
                emit({"event": "frontier"})
                await asyncio.Event().wait()
            return result

        result = await super().run(actor, session_id, intercept, write=write)
        if self.armed and write and self.mode == "after_commit":
            emit({"event": "frontier"})
            await asyncio.Event().wait()
        return result


async def main():
    config = json.loads(await asyncio.to_thread(sys.stdin.readline))
    actor = Actor(id=config["owner"])

    @asynccontextmanager
    async def source(caller, session_id):
        assert caller.id == actor.id
        async with await AsyncConnection.connect(config["connection"]) as connection:
            yield connection

    root = await FaultRoot.open(source, actor=actor, schema=config["schema"])
    root.mode = config["mode"]
    store = PostgresSessionStore(root).for_actor(actor)
    table = sql.Identifier(config["schema"], "application_effects")

    async def apply(transaction, binding, caller):
        await transaction.execute("write", [binding.settlement_id])
        root.armed = True
        if root.mode == "participant":
            emit({"event": "frontier"})
            await asyncio.Event().wait()

    statement = (
        "INSERT INTO {} VALUES (%s)"
        if config["decision"] == "commit"
        else "DELETE FROM {} WHERE id=%s"
    )
    participant = PostgresSettlementParticipant(
        root,
        id="fabricated",
        operations={"write": sql.SQL(statement).format(table).as_string()},
        apply=apply,
    )
    async with store.turn(config["session"], actor.id, turn_id="crash-operation") as log:
        history = PostgresHistory(root).for_turn(log)
        ref = await history.put(
            actor=actor,
            session_id=config["session"],
            key="crash-content",
            record=HistoryRecord(
                kind="message", message=Message(role="user", content="Fabricated crash content")
            ),
        )
        emit({"event": "staged", "ref": ref.model_dump(mode="json")})
        if root.mode == "staged":
            emit({"event": "frontier"})
            await asyncio.Event().wait()
        binding = SettlementBinding(
            settlement_id="fabricated-settlement",
            invocation_id="fabricated-invocation",
            participant=participant.id,
            stage_id="fabricated-stage",
            owner=actor.id,
            session_id=config["session"],
            actor_id=actor.id,
            principal=actor.id,
            spec_digest="a" * 64,
            result_digest="b" * 64,
            decision=config["decision"],
        )
        terminal = "committed" if binding.decision == "commit" else "rolled_back"
        await log.settle(
            [
                NewEntry(
                    kind="message",
                    key="atomic-message",
                    history_ref=ref,
                    facts={"role": "user", "is_error": False},
                ),
                NewEntry(
                    kind="settlement",
                    key="settlement:fabricated-settlement:pending",
                    facts={"binding": binding.model_dump(mode="json"), "status": "pending"},
                ),
                NewEntry(
                    kind="settlement",
                    key="settlement:fabricated-settlement:" + terminal,
                    facts={"binding": binding.model_dump(mode="json"), "status": terminal},
                ),
            ],
            binding,
            participant,
        )


if __name__ == "__main__":
    asyncio.run(main())
