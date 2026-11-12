"""Child process that pauses after a fabricated tool effect and before its result."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from process_lifetime_controls import model_execution_timeouts
from test_gated_execution import DemoTool
from test_resume_engine import build

from tether.history.jsonl import JsonlHistory
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor
from tether.types.storage import SessionInit


def emit(value: dict) -> None:
    print(json.dumps(value), flush=True)


def record_effect(path: Path, invocation_id: str | None = None) -> None:
    record = (invocation_id.encode() if invocation_id else b"fabricated-effect") + b"\n"
    if invocation_id and path.exists() and record in path.read_bytes().splitlines(keepends=True):
        return
    with path.open("ab") as target:
        target.write(record)
        target.flush()
        os.fsync(target.fileno())


class EffectTool(DemoTool):
    def __init__(
        self,
        path: Path,
        timeout_values: list[float | None] | None = None,
        model_timeout_seconds: float | None = None,
        *,
        safe: bool = False,
        hold: bool = True,
        minimum_model_timeouts: int = 1,
    ) -> None:
        super().__init__()
        self.path = path
        self.safe = safe
        self.hold = hold
        self.timeout_values = timeout_values
        self.model_timeout_seconds = model_timeout_seconds
        self.tool_timeout_seconds: float | None = None
        self.minimum_model_timeouts = minimum_model_timeouts
        self.timeout_snapshot: tuple[float | None, ...] = ()

    async def run(self, ctx, args) -> str:
        self.calls.append((ctx.invocation_id, args.model_dump()))
        self.timeout_snapshot = (
            tuple(self.timeout_values) if self.timeout_values is not None else ()
        )
        if self.timeout_values is not None:
            assert self.model_timeout_seconds is not None
            assert (
                self.timeout_values.count(self.model_timeout_seconds) >= self.minimum_model_timeouts
            ), repr(self.timeout_snapshot)
            assert self.timeout_values[-1] == self.tool_timeout_seconds, repr(
                (self.timeout_values, self.tool_timeout_seconds)
            )
        await asyncio.to_thread(record_effect, self.path, ctx.invocation_id if self.safe else None)
        if self.hold:
            emit({"event": "effect_committed", "invocation_id": ctx.invocation_id})
            await asyncio.Event().wait()
        return "Recorded fabricated effect"


async def main(root_path: Path, effect_path: Path, *, safe: bool = False) -> None:
    with model_execution_timeouts() as timeout_values:
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
        tool = EffectTool(
            effect_path,
            timeout_values,
            limits.request_timeout_seconds,
            safe=safe,
            minimum_model_timeouts=2,
        )
        run = build(h, safe=safe, tool=tool)
        tool.tool_timeout_seconds = run.engine.limits.tool_timeout_seconds
        session = await store.create(actor.id, SessionInit(settings={"approval": "auto"}))
        emit({"event": "created", "session_id": session.id})
        async with store.turn(session.id, actor.id, turn_id="killed-turn") as log:
            await run.engine.run_turn(actor, log, "Fabricated request", spec=run.spec)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]), safe=len(sys.argv) > 3))
