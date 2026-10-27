"""Child process that pauses after a fabricated tool effect and before its result."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from test_gated_execution import DemoTool
from test_resume_engine import build

from tether.history.jsonl import JsonlHistory
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor
from tether.types.storage import SessionInit


def emit(value: dict) -> None:
    print(json.dumps(value), flush=True)


def record_effect(path: Path) -> None:
    with path.open("ab") as target:
        target.write(b"fabricated-effect\n")
        target.flush()
        os.fsync(target.fileno())


class EffectTool(DemoTool):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    async def run(self, ctx, args) -> str:
        await asyncio.to_thread(record_effect, self.path)
        emit({"event": "effect_committed", "invocation_id": ctx.invocation_id})
        await asyncio.Event().wait()
        return "unreachable"


async def main(root_path: Path, effect_path: Path) -> None:
    root = await JsonlRoot.open(root_path)
    store = await JsonlSessionStore.open(root)
    provider = await JsonlHistory.open(root)
    actor = Actor(id="fabricated-owner")
    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )
    h = SimpleNamespace(store=store, provider=provider, actor=actor, engines=[], limits=limits)
    run = build(h, tool=EffectTool(effect_path))
    session = await store.create(actor.id, SessionInit(settings={"approval": "auto"}))
    emit({"event": "created", "session_id": session.id})
    async with store.turn(session.id, actor.id, turn_id="killed-turn") as log:
        await run.engine.run_turn(actor, log, "Fabricated request", spec=run.spec)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
