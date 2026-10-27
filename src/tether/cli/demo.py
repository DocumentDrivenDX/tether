"""Offline local profile used by the reference clients.

The scripted conversation and tool write only fabricated values. No provider or
credential is discovered from the process environment.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel, JsonValue

from tether import Tether
from tether.context.testing import fabricated_context
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import ModelExecutionLimits, ModelResponse, TextDelta, ToolCall
from tether.types.client import ClientLimits
from tether.types.execution import Actor, AgentSpec, Effect, Replay, ToolContext


class DemoArguments(BaseModel):
    label: str


class DemoTool:
    name = "record_demo"
    version = "1"
    description = "Record a fabricated demo label."
    input_model: type[BaseModel] = DemoArguments
    requires: frozenset[str] = frozenset()
    model_visible = True
    always_ask = True
    effect: Effect = "effectful"
    replay: Replay = "unsafe"
    timeout_seconds: float | None = None
    definition_hash: str | None = None

    def __init__(self) -> None:
        self.traits: dict[str, JsonValue] = {}
        self.recorded: list[str] = []

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: BaseModel) -> str:
        label = DemoArguments.model_validate(args).label
        self.recorded.append(label)
        return f"Recorded fabricated label: {label}"


DEMO_SPEC = AgentSpec(
    id="demo",
    version="1",
    title="Tether demo",
    instructions="This is a fabricated offline demonstration.",
    model="demo",
    tools=(DemoTool.name,),
)


def demo_model(*, continuation: bool = False) -> ScriptedModel:
    answer = (
        TextDelta(text="The fabricated "),
        TextDelta(text="record is complete."),
        ModelResponse(content="The fabricated record is complete.", finish_reason="stop"),
    )
    proposal = ModelResponse(
        tool_calls=(ToolCall(id="demo-call", name=DemoTool.name, arguments={"label": "seedling"}),),
        finish_reason="tool_calls",
    )
    cycle = (proposal, *answer)
    return ScriptedModel(((*answer, *cycle) if continuation else cycle) * 32)


def client_limits() -> ClientLimits:
    return ClientLimits(
        max_page_size=100,
        max_call_ids=8,
        max_read_bytes=1024 * 1024,
        max_lookup_entries=1000,
        read_timeout_seconds=5,
        heartbeat_interval_seconds=1,
        shutdown_timeout_seconds=5,
        follower_queue_size=32,
        max_followers=8,
        max_active_turns=8,
        max_request_bytes=65536,
    )


@asynccontextmanager
async def open_demo(
    path: Path, *, continuation: bool = False, volatile: bool = False
) -> AsyncGenerator[tuple[Tether, Actor, DemoTool]]:
    root = None if volatile else await JsonlRoot.open(path)
    files = MemoryFiles() if volatile else root
    if files is None:
        raise RuntimeError("demo_store_unavailable")
    try:
        store = await JsonlSessionStore.open(files)
        history = await JsonlHistory.open(files)
        tool = DemoTool()
        engine = Engine(
            **fabricated_context(),
            model=demo_model(continuation=continuation),
            history=MessageHistoryAdapter(history),
            history_provider=history,
            model_limits=ModelExecutionLimits(
                max_response_bytes=8192,
                max_tool_argument_bytes=512,
                max_attempts=2,
                request_timeout_seconds=5,
                max_retry_delay_seconds=0.01,
            ),
            tools=(tool,),
            specs=(DEMO_SPEC,),
        )
        async with Tether(
            engine=engine,
            store=store,
            history=history,
            limits=client_limits(),
            resume="off" if volatile else "report",
            default_spec=DEMO_SPEC.id,
        ) as app:
            yield app, Actor(id="demo"), tool
    finally:
        if root is not None:
            await root.aclose()
