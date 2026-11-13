"""Child that pauses after accepted context use, before the main model response."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from process_lifetime_controls import summary_execution_controls
from test_agent_summaries import response, turn
from test_agent_summary_restart import open_state

from tether.models.testing import ScriptedModel
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor


def emit(value: dict[str, str]) -> None:
    print(json.dumps(value), flush=True)


class PausedModel:
    provider = "scripted"

    def __init__(
        self,
        phase: str,
        model_timeouts: list[float | None],
        context_deadlines: list[float],
        model_timeout_seconds: float,
        context_timeout_seconds: float,
    ) -> None:
        self.scripted = ScriptedModel(
            [response("Fabricated old answer"), response("Accepted summary", 7)]
        )
        self.phase = phase
        self.count = 0
        self.model_timeouts = model_timeouts
        self.context_deadlines = context_deadlines
        self.model_timeout_seconds = model_timeout_seconds
        self.context_timeout_seconds = context_timeout_seconds

    async def stream(self, request):
        self.count += 1
        if self.count == (2 if self.phase == "before_summary" else 3):
            assert self.model_timeouts.count(self.model_timeout_seconds) >= 2
            assert any(
                deadline - 1000.0 == self.context_timeout_seconds
                for deadline in self.context_deadlines
            )
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
    context_timeout_seconds = h.engine.context.config.timeout_seconds
    with summary_execution_controls(h.engine.context.config) as (
        model_timeouts,
        context_deadlines,
    ):
        h.engine.model = PausedModel(
            phase,
            model_timeouts,
            context_deadlines,
            limits.request_timeout_seconds,
            context_timeout_seconds,
        )
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
                    assert any(
                        deadline - 1000.0 == context_timeout_seconds
                        for deadline in context_deadlines
                    )
                    emit({"event": "summary_committed"})
                    threading.Event().wait()
                return offset

            h.root._append = pause_after_compaction
        await turn(h, "second")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
