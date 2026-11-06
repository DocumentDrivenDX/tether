"""Explicit fabricated model/tool composition for actual browser acceptance."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel

from tether.client import (
    AgentSpec,
    ClientHost,
    ClientLimits,
    ClientSettings,
    Effect,
    JsonValue,
    LocalJsonlSettings,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    Replay,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolContext,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model

MAX_FUNCTIONAL_WATCHDOG_SECONDS = (2**31 - 1) / 1000


class Arguments(BaseModel):
    label: str


class ApprovalTool:
    name = "record_demo"
    version = "1"
    description = "Record a fabricated demo label."
    input_model: type[BaseModel] = Arguments
    requires: frozenset[str] = frozenset()
    model_visible = True
    always_ask = True
    effect: Effect = "effectful"
    replay: Replay = "unsafe"
    timeout_seconds: float | None = None
    definition_hash: str | None = None

    def __init__(self) -> None:
        self.traits: dict[str, JsonValue] = {}

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: BaseModel) -> str:
        return f"Recorded fabricated label: {Arguments.model_validate(args).label}"


class BrowserModel:
    provider = "fabricated-browser"

    def __init__(
        self,
        pause_signal: Path | None = None,
        pause_event: asyncio.Event | None = None,
    ) -> None:
        self.held_once = False
        self.pause_signal = pause_signal
        self.pause_event = pause_event

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        current = request.messages[-1]
        hold = current.content.startswith("Hold this fabricated turn")
        recovery = current.content.startswith("Recover this interrupted fabricated turn")
        if current.role == "tool" or hold or recovery:
            response = ModelResponse(
                content="The fabricated record is complete.", finish_reason="stop"
            )
        else:
            response = ModelResponse(
                tool_calls=(
                    ToolCall(
                        id="fabricated-call", name="record_demo", arguments={"label": "seedling"}
                    ),
                ),
                finish_reason="tool_calls",
            )
        pause = recovery if self.pause_signal is not None else hold and not self.held_once
        self.held_once = self.held_once or hold
        script = (TextDelta(text=response.content), response) if response.content else (response,)
        model = scripted_model(script)
        async for item in model.stream(request):
            yield item
            if pause and isinstance(item, TextDelta):
                if self.pause_signal is None:
                    await asyncio.Future()
                else:
                    self.pause_signal.write_text("model paused after provisional text\n")
                    if self.pause_event is not None:
                        self.pause_event.set()
                    print("BROWSER_RECOVERY_READY", flush=True)
                    await asyncio.Future()


async def browser_host(
    path: Path,
    *,
    pause_signal: Path | None = None,
    pause_event: asyncio.Event | None = None,
) -> ClientHost:
    context = fabricated_model_context()
    return await ClientHost.open(
        ClientSettings(
            model=BrowserModel(pause_signal, pause_event),
            storage=LocalJsonlSettings(path),
            policy=demo_policy(),
            context_config=context["context_config"],
            estimator=context["estimator"],
            client_limits=ClientLimits(
                max_page_size=100,
                max_call_ids=8,
                max_read_bytes=1_048_576,
                max_lookup_entries=1000,
                read_timeout_seconds=5,
                heartbeat_interval_seconds=0.1,
                shutdown_timeout_seconds=5,
                follower_queue_size=32,
                max_followers=8,
                max_active_turns=8,
                max_request_bytes=65536,
            ),
            model_limits=ModelExecutionLimits(
                max_response_bytes=8192,
                max_tool_argument_bytes=512,
                max_attempts=1,
                # The independent framework/job owner supplies the useful
                # operational watchdog. This fabricated request has no separate
                # 30-second product deadline to assert.
                request_timeout_seconds=MAX_FUNCTIONAL_WATCHDOG_SECONDS,
                max_retry_delay_seconds=0.01,
            ),
            specs=(
                AgentSpec(
                    id="demo",
                    version="1",
                    title="Browser fixture",
                    model="demo",
                    instructions="Fabricated browser acceptance only.",
                    tools=("record_demo",),
                ),
            ),
            tools=(ApprovalTool(),),
            default_spec="demo",
        )
    )
