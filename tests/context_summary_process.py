"""Child that pauses after accepted context use, before the main model response."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from test_agent_summaries import response, turn
from test_agent_summary_restart import open_state

from tether.models.testing import ScriptedModel
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor


def emit(value: dict[str, str]) -> None:
    print(json.dumps(value), flush=True)


class PausedModel:
    provider = "scripted"

    def __init__(self, phase: str) -> None:
        self.scripted = ScriptedModel(
            [response("Fabricated old answer"), response("Accepted summary", 7)]
        )
        self.phase = phase
        self.count = 0

    async def stream(self, request):
        self.count += 1
        if self.count == (2 if self.phase == "before_summary" else 3):
            emit(
                {
                    "event": "model_requested",
                    "model_call_id": request.tags.model_call_id,
                    "purpose": request.tags.purpose,
                }
            )
            await asyncio.Event().wait()
            return
        async for item in self.scripted.stream(request):
            yield item


async def main(path: Path, phase: str) -> None:
    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )
    h = await open_state(path, [datetime.now(UTC)], limits, Actor(id="fabricated-owner"))
    h.engine.model = PausedModel(phase)
    assert (await turn(h, "first"))[0].stop_reason == "final"
    emit({"event": "created", "session_id": h.session.id})
    if phase == "after_summary_commit":
        original = h.root._append

        def pause_after_compaction(name, value, guard):
            offset = original(name, value, guard)
            if (
                name == "journal"
                and isinstance(value, dict)
                and any(e.get("kind") == "compaction" for e in value.get("entries", []))
            ):
                emit({"event": "summary_committed"})
                threading.Event().wait()
            return offset

        h.root._append = pause_after_compaction
    await turn(h, "second")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
