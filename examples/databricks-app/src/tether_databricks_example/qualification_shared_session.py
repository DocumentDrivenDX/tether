"""Opt-in fabricated approval/effect scenario for shared Apps qualification."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from threading import Event
from typing import Literal

from pydantic import BaseModel, JsonValue

from tether.client import (
    AgentSpec,
    ClientSettings,
    Effect,
    ModelRequest,
    ModelResponse,
    ProvidedStorage,
    Replay,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolContext,
)

from .qualification import qualification_client_settings

_FINAL = "Fabricated Tether qualification effect completed."


class QualificationArguments(BaseModel):
    scenario: Literal["shared-session", "unknown-effect"]


class QualificationEffect:
    """A fabricated, approval-gated effect that can model an unknown outcome."""

    name = "record_qualification"
    version = "1"
    description = "Record a fabricated qualification effect."
    input_model: type[BaseModel] = QualificationArguments
    requires: frozenset[str] = frozenset()
    model_visible = True
    always_ask = True
    effect: Effect = "effectful"
    replay: Replay = "unsafe"
    timeout_seconds: float | None = None
    definition_hash: str | None = None

    def __init__(
        self,
        *,
        started: Event | None = None,
        release_unknown: Event | None = None,
        unknown_hold_seconds: float = 300,
    ) -> None:
        self.traits: dict[str, JsonValue] = {}
        self.started = started
        self.release_unknown = release_unknown
        self.unknown_hold_seconds = unknown_hold_seconds
        self.timeout_seconds = unknown_hold_seconds + 10

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        del ctx, args
        return True

    def run(self, ctx: ToolContext, args: BaseModel) -> str:
        value = QualificationArguments.model_validate(args)
        print(f"TETHER_QUALIFICATION_WORKER_STARTED {ctx.invocation_id}", flush=True)
        if value.scenario == "unknown-effect":
            if self.started is not None:
                self.started.set()
            release_unknown = self.release_unknown
            if release_unknown is None:
                time.sleep(self.unknown_hold_seconds)
            else:
                release_unknown.wait(timeout=self.unknown_hold_seconds)
        else:
            del ctx
        return _FINAL


class SharedSessionQualificationModel:
    """Ask for one approval, then return a fixed result after its tool message."""

    provider = "fabricated-qualification"

    def __init__(self) -> None:
        self.tool_calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        self.tool_calls += 1
        tool_message = next((m for m in reversed(request.messages) if m.role == "tool"), None)
        if tool_message is not None:
            yield TextDelta(text=_FINAL)
            yield ModelResponse(content=_FINAL, finish_reason="stop")
            return

        current = next((m for m in reversed(request.messages) if m.role == "user"), None)
        scenario: Literal["shared-session", "unknown-effect"] = (
            "unknown-effect"
            if current is not None
            and current.content.strip() == "Run the Tether qualification scenario: unknown-effect."
            else "shared-session"
        )
        yield ModelResponse(
            tool_calls=(
                ToolCall(
                    id=f"qualification-call-{self.tool_calls}",
                    name="record_qualification",
                    arguments={"scenario": scenario},
                ),
            ),
            finish_reason="tool_calls",
        )


def qualification_shared_session_settings(
    storage: ProvidedStorage,
    *,
    hold_seconds: float = 0,
    unknown_hold_seconds: float = 300,
    started: Event | None = None,
    release_unknown: Event | None = None,
) -> ClientSettings:
    """Build an explicit test-only, shared-session model/tool composition.

    Ordinary messages request one approval-gated fabricated effect. The exact
    reserved unknown-effect prompt leaves that effect running after approval,
    allowing a process-restart profile to verify that Tether records an unknown
    external outcome rather than asserting success or replaying it. The default
    physical hold exceeds the bounded platform-stop observation window; elapsed
    time alone never establishes outstanding work or an unknown outcome.
    """
    settings = qualification_client_settings(storage, hold_seconds=hold_seconds)
    if not 15 <= unknown_hold_seconds <= 600:
        raise ValueError("qualification_unknown_hold_out_of_range")
    tool = QualificationEffect(
        started=started,
        release_unknown=release_unknown,
        unknown_hold_seconds=unknown_hold_seconds,
    )
    spec = AgentSpec(
        id="qualification",
        version="1",
        title="Fabricated shared-session qualification",
        instructions="Request the configured fabricated qualification effect.",
        model="fabricated-qualification",
        tools=(tool.name,),
    )
    return ClientSettings(
        model=SharedSessionQualificationModel(),
        storage=settings.storage,
        policy=settings.policy,
        client_limits=settings.client_limits,
        model_limits=settings.model_limits,
        context_config=settings.context_config,
        estimator=settings.estimator,
        specs=(spec,),
        tools=(tool,),
        identity_mode=settings.identity_mode,
        resume=settings.resume,
        default_spec=spec.id,
    )


__all__ = [
    "QualificationArguments",
    "QualificationEffect",
    "SharedSessionQualificationModel",
    "qualification_shared_session_settings",
]
