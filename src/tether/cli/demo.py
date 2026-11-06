"""Offline local profile used by the reference clients.

The scripted conversation and tool write only fabricated values. No provider or
credential is discovered from the process environment.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, JsonValue

from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientLimits,
    ClientSettings,
    ContextConfig,
    Effect,
    LocalJsonlSettings,
    ModelExecutionLimits,
    ModelPort,
    ModelResponse,
    Replay,
    TextDelta,
    TokenEstimator,
    Tool,
    ToolCall,
    ToolContext,
    VolatileSettings,
)
from tether.client.testing import (
    demo_policy,
    fabricated_model_context,
    replay_model,
    scripted_model,
)

from .common_tools import common_tools
from .configured import configured_model
from .local import LocalModelOptions as LocalModelOptions
from .local import resolve_profile as resolve_profile
from .profile import ReferenceModelProfile as ReferenceModelProfile
from .profile import load_profile as load_profile

if TYPE_CHECKING:
    from tether.client.compatible import CompatibleModel


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


def demo_model(*, continuation: bool = False) -> ModelPort:
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
    return scripted_model(((*answer, *cycle) if continuation else cycle) * 32)


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
    path: Path,
    *,
    continuation: bool = False,
    volatile: bool = False,
    profile: ReferenceModelProfile | None = None,
    replay_cassette: str | None = None,
) -> AsyncGenerator[tuple[ClientHost, Actor, DemoTool]]:
    adapter: CompatibleModel | None = None
    try:
        tool = DemoTool()
        tools: tuple[Tool, ...] = (tool,)
        if profile is not None and profile.workspace is not None:
            import asyncio

            if not await asyncio.to_thread(profile.workspace.is_dir):
                raise ValueError("workspace_directory_required")
            tools += common_tools(profile.workspace)
        model_limits = ModelExecutionLimits(
            max_response_bytes=profile.max_response_bytes if profile is not None else 8192,
            max_tool_argument_bytes=65536 if len(tools) > 1 else 512,
            max_attempts=2,
            request_timeout_seconds=30 if profile is not None else 5,
            max_retry_delay_seconds=0.25 if profile is not None else 0.01,
        )
        actor = Actor(id="demo")
        spec = DEMO_SPEC
        offline_context = fabricated_model_context()
        context_config: ContextConfig = offline_context["context_config"]
        estimator: TokenEstimator = offline_context["estimator"]
        model: ModelPort = demo_model(continuation=continuation)
        if profile is not None:
            adapter, context_config, estimator, actor, spec = await configured_model(
                profile, model_limits, tuple(item.name for item in tools)
            )
            model = adapter
        if replay_cassette is not None:
            model = replay_model(replay_cassette)
        settings = ClientSettings(
            context_config=context_config,
            estimator=estimator,
            model=model,
            storage=VolatileSettings() if volatile else LocalJsonlSettings(path),
            policy=demo_policy(),
            client_limits=client_limits(),
            model_limits=model_limits,
            identity_mode=profile.identity_mode if profile is not None else "caller",
            tools=tools,
            specs=(spec,),
            resume="off" if volatile else "report",
            default_spec=spec.id,
        )
        async with await ClientHost.open(settings) as app:
            yield app, actor, tool
    finally:
        if adapter is not None:
            await adapter.aclose()
