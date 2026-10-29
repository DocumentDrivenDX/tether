"""Two-transaction settlement peer with observable durable application acknowledgments."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from typing import Literal

from psycopg import AsyncConnection, sql
from test_consumer_settlement import StagedTool, Withhold

from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.postgres import DatabaseRoot, PostgresHistory
from tether.stores.postgres.store import PostgresSessionStore
from tether.types.canonical import digest
from tether.types.commands import InvokeTool
from tether.types.execution import Actor, AgentSpec, ExecutionDenied
from tether.types.settlement import SettlementBinding, SettlementOutcome


class IndependentSQL:
    id = "fabricated"
    profile: Literal["independent", "shared"] = "independent"

    def __init__(self, root: DatabaseRoot, *, pause: str | None = None):
        self.root, self.pause = root, pause

    async def settle(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        if actor.id != binding.actor_id or binding.owner != actor.id:
            raise ExecutionDenied("settlement_scope")
        if self.pause == "decision":
            print(json.dumps(binding.model_dump(mode="json")), flush=True)
            await asyncio.Event().wait()

        async def write(connection):
            table = sql.Identifier(self.root.schema, "application_effects")
            fingerprint = digest(binding.model_dump(mode="json"))
            await connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,%s) ON CONFLICT DO NOTHING").format(table),
                (binding.settlement_id, fingerprint),
            )
            cursor = await connection.execute(
                sql.SQL("SELECT binding FROM {} WHERE id=%s").format(table),
                (binding.settlement_id,),
            )
            if await cursor.fetchone() != (fingerprint,):
                raise ExecutionDenied("settlement_conflict")

        await self.root.run(actor, binding.session_id, write, write=True)
        if self.pause == "effect":
            print(json.dumps(binding.model_dump(mode="json")), flush=True)
            await asyncio.Event().wait()
        return "committed" if binding.decision == "commit" else "rolled_back"

    async def reconcile(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        return await self.settle(binding, actor)


async def main():
    config = json.loads(await asyncio.to_thread(sys.stdin.readline))
    actor = Actor(id=config["owner"])

    @asynccontextmanager
    async def source(caller, session_id):
        assert caller.id == actor.id
        async with await AsyncConnection.connect(config["connection"]) as connection:
            yield connection

    root = await DatabaseRoot.open(source, actor=actor, schema=config["schema"])
    provider = PostgresHistory(root)
    tool = StagedTool()
    engine = Engine(
        model=ScriptedModel([]),
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        tools=(tool,),
        settlements=(IndependentSQL(root, pause=config["mode"]),),
        guards=(Withhold(),) if config["decision"] == "rollback" else (),
        **fabricated_context(),
    )
    spec = AgentSpec(
        id="fixture",
        version="1",
        title="Fixture",
        instructions="",
        model="fixture",
        tools=(tool.name,),
    )
    async with (
        PostgresSessionStore(root)
        .for_actor(actor)
        .turn(config["session"], actor.id, turn_id="killed-operation") as log
    ):
        await engine.commands.invoke(
            actor,
            log,
            InvokeTool(request_id="write", tool_name="write", arguments={"count": 1}),
            spec=spec,
        )


if __name__ == "__main__":
    asyncio.run(main())
