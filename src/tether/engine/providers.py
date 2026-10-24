"""Narrow-only extension hooks and their bounded no-op default."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import cast

from tether.context.provenance import SourceProjection, align_sources
from tether.types import Message, ModelRequest, Payload, ToolCall
from tether.types.context import ContextSource
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
        return await prepared_hook(gov, workers, provider, method, tuple(_copy(a) for a in args))
    except Exception:
        raise ExecutionDenied(f"x.{provider.id}.provider_error") from None


async def prepared_hook(
    gov: Governance, workers: Workers, provider: Provider, method: str, args: tuple[object, ...]
) -> object:
    try:
        with gov.observers.steps.provider(gov.scope, provider, method):
            async with asyncio.timeout(gov.limits.hook_timeout_seconds):
                return await workers.call(gov.log.session.id, getattr(provider, method), *args)
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


async def narrow_sources(
    gov: Governance,
    workers: Workers,
    providers: tuple[Provider, ...],
    spec: AgentSpec,
    request: ModelRequest,
    sources: tuple[ContextSource, ...],
) -> tuple[ModelRequest, SourceProjection]:
    system, tools = request.system, request.tools
    filtered: set[int] = set()
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
        supplied = tuple(source.message.model_copy(deep=True) for source in sources)
        narrowed = await prepared_hook(
            gov,
            workers,
            provider,
            "before_request",
            (_copy(gov.log.session), _copy(spec), supplied),
        )
        projection = align_sources(sources, supplied, narrowed)
        sources = projection.sources
        filtered.update(projection.filtered_indices)
    request = request.model_copy(
        update={
            "messages": tuple(s.message.model_copy(deep=True) for s in sources),
            "system": system,
            "tools": tools,
        }
    )
    return request, SourceProjection(sources, tuple(sorted(filtered)))
