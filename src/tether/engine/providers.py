"""Narrow-only extension hooks and their bounded no-op default."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import cast

from tether.types import Message, ModelRequest, Payload, ToolCall
from tether.types.execution import (
    Actor,
    AgentSpec,
    ExecutionDenied,
    Provider,
    Refusal,
    SessionState,
    Tool,
    copy_actor,
)

from .governance import Governance
from .workers import Workers


class BaseProvider:
    id = "base"
    contract = 1

    def tools(self) -> Sequence[Tool]:
        return ()

    async def capabilities(self, actor: Actor, session: SessionState) -> Mapping[type, object]:
        return {}

    async def prompt_section(self, session: SessionState, spec: AgentSpec) -> str | None:
        return None

    async def hidden_tools(self, session: SessionState, spec: AgentSpec) -> frozenset[str]:
        return frozenset()

    async def before_request(
        self, session: SessionState, spec: AgentSpec, messages: tuple[Message, ...]
    ) -> tuple[Message, ...]:
        return messages

    async def before_call(
        self, session: SessionState, spec: AgentSpec, call: ToolCall
    ) -> Refusal | None:
        return None

    async def after_call(
        self, session: SessionState, spec: AgentSpec, call: ToolCall, message: Message
    ) -> Message:
        return message


async def provider_hook(
    gov: Governance, workers: Workers, provider: Provider, method: str, *args: object
) -> object:
    try:
        copies = [_copy(a) for a in args]
        async with asyncio.timeout(gov.limits.hook_timeout_seconds):
            return await workers.call(gov.log.session.id, getattr(provider, method), *copies)
    except Exception:
        raise ExecutionDenied(f"x.{provider.id}.provider_error") from None


def _copy(value: object) -> object:
    if isinstance(value, Actor):
        return copy_actor(value)
    if isinstance(value, Payload):
        return value.model_copy(deep=True)
    if isinstance(value, tuple):
        return tuple(_copy(v) for v in cast(tuple[object, ...], value))
    return value


def structural_subset(original: tuple[Message, ...], narrowed: object) -> tuple[Message, ...]:
    if not isinstance(narrowed, tuple) or not all(
        isinstance(m, Message) for m in cast(tuple[object, ...], narrowed)
    ):
        raise ExecutionDenied("provider_output_invalid")
    narrowed = cast(tuple[Message, ...], narrowed)
    remaining = iter(original)
    for message in narrowed:
        found = False
        for source in remaining:
            if (message.role, message.tool_call_id, message.tool_calls, message.is_error) == (
                source.role,
                source.tool_call_id,
                source.tool_calls,
                source.is_error,
            ) and all(a in source.attachments for a in message.attachments):
                found = True
                break
        if not found:
            raise ExecutionDenied("provider_output_invalid")
    return narrowed


async def narrow_request(
    gov: Governance,
    workers: Workers,
    providers: tuple[Provider, ...],
    spec: AgentSpec,
    request: ModelRequest,
) -> ModelRequest:
    messages, system, tools = request.messages, request.system, request.tools
    for provider in providers:
        section = await provider_hook(
            gov, workers, provider, "prompt_section", gov.log.session, spec
        )
        hidden = await provider_hook(gov, workers, provider, "hidden_tools", gov.log.session, spec)
        if section is not None and not isinstance(section, str):
            raise ExecutionDenied("provider_output_invalid")
        if not isinstance(hidden, frozenset) or not all(
            isinstance(v, str) for v in cast(frozenset[object], hidden)
        ):
            raise ExecutionDenied("provider_output_invalid")
        system += "\n\n" + section if section else ""
        tools = tuple(t for t in tools if t.name not in hidden)
        narrowed = await provider_hook(
            gov, workers, provider, "before_request", gov.log.session, spec, messages
        )
        messages = structural_subset(messages, narrowed)
    return request.model_copy(update={"messages": messages, "system": system, "tools": tools})
