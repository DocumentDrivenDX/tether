"""Agent's generic Model delegates only through the Tether model boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse as SDKResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset

from tether.models.canonical import copy_request
from tether.ports import ModelPort
from tether.types import (
    ModelAttemptDenied,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ToolSpec,
)

from .attachments import projected
from .messages import from_sdk_messages, to_sdk_messages, to_sdk_response
from .response import bounded_response

PrepareModel = Callable[[ModelRequest], Awaitable[ModelRequest]]


class PortModel(Model):
    """One run-local bridge; mandatory preparation owns current read/context/policy gates."""

    def __init__(
        self,
        port: ModelPort,
        template: ModelRequest,
        limits: ModelExecutionLimits,
        prepare: PrepareModel,
        accept_response: Callable[[ModelResponse], Awaitable[None]] | None = None,
        observe_model: Callable[[ModelRequest], AbstractContextManager[str | None]] | None = None,
    ) -> None:
        super().__init__()
        self._port = port
        self._template = copy_request(template)
        self._limits = limits
        self._prepare = prepare
        self._accept_response = accept_response
        self._observe_model = observe_model
        self._calls: set[tuple[str, str, str]] = set()
        self.last_response: ModelResponse | None = None

    @property
    def model_name(self) -> str:
        return self._template.model

    @property
    def system(self) -> str:
        return "tether-model-port"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> SDKResponse:
        settings, parameters = self.prepare_request(model_settings, model_request_parameters)
        if (
            settings
            or parameters.native_tools
            or parameters.output_tools
            or parameters.output_object
        ):
            raise ModelAttemptDenied("unsupported_model_configuration")
        system, converted = from_sdk_messages(messages)
        tools = tuple(
            ToolSpec(
                name=t.name, description=t.description or "", input_schema=t.parameters_json_schema
            )
            for t in parameters.function_tools
        )
        candidate = self._template.model_copy(
            update={"messages": converted, "system": system, "tools": tools}, deep=False
        )
        prepared = await self._prepare(candidate)
        prepared = ModelRequest.model_validate(
            prepared.model_dump(mode="python")
            | {
                "credential": prepared.credential,
                "attempts": prepared.attempts,
            }
        )
        to_sdk_messages(prepared.messages, prepared.system, require_closed=True)
        tags = prepared.tags
        key = (tags.session_id, tags.turn_id, tags.model_call_id)
        if key in self._calls or prepared.attempts is None:
            raise ModelAttemptDenied("invalid_model_call_binding")
        self._calls.add(key)
        context = (
            self._observe_model(prepared)
            if self._observe_model is not None
            else nullcontext(prepared.traceparent)
        )
        with context as traceparent:
            return await self._accepted_response(
                prepared.model_copy(update={"traceparent": traceparent})
            )

    async def _accepted_response(self, prepared: ModelRequest) -> SDKResponse:
        seconds = min(
            prepared.timeout_seconds or self._limits.request_timeout_seconds,
            self._limits.request_timeout_seconds,
        )
        try:
            async with asyncio.timeout(seconds):
                response = await self._bounded_response(prepared)
        except TimeoutError:
            raise ModelUnavailable("timeout") from None
        if self._accept_response is not None:
            await self._accept_response(response.model_copy(deep=True))
        self.last_response = response.model_copy(deep=True)
        return to_sdk_response(response, prepared.model)

    async def _bounded_response(self, request: ModelRequest) -> ModelResponse:
        return await bounded_response(self._port, request, self._limits)


@dataclass(frozen=True)
class AgentModelResult:
    """Observed response only. SDK projections do not establish durable acceptance."""

    response: ModelResponse
    messages: tuple[ModelMessage, ...]


async def run_agent(
    port: ModelPort,
    request: ModelRequest,
    limits: ModelExecutionLimits,
    *,
    prepare: PrepareModel,
    authorize_history: Callable[[], Awaitable[None]],
) -> AgentModelResult:
    """Slice 1: text or deferred proposals; no function may dispatch before slice 2 gates."""
    await authorize_history()
    request = copy_request(request)

    async def restore(candidate: ModelRequest) -> ModelRequest:
        await authorize_history()
        if candidate.messages != projected(request.messages) or candidate.system != request.system:
            raise ModelAttemptDenied("derived_history_changed")
        return await prepare(
            copy_request(candidate.model_copy(update={"messages": request.messages}))
        )

    bridge = PortModel(port, request, limits, restore)
    definitions = [
        ToolDefinition(
            name=t.name, description=t.description, parameters_json_schema=t.input_schema
        )
        for t in request.tools
    ]
    agent = Agent(
        bridge,
        output_type=[str, DeferredToolRequests],
        toolsets=[ExternalToolset(definitions)],
        retries=0,
    )
    agent.instrument = False
    history = to_sdk_messages(request.messages, request.system)
    result = None
    failure: str | None = None
    try:
        result = await agent.run(None, message_history=history, infer_name=False)
    except UnexpectedModelBehavior:
        observed = bridge.last_response
        failure = (
            "empty_response"
            if observed and not observed.content and not observed.tool_calls
            else "invalid_response"
        )
    if failure is not None:
        raise ModelUnavailable(failure)
    if result is None:
        raise ModelUnavailable("invalid_response")
    if bridge.last_response is None:
        raise ModelAttemptDenied("missing_model_response")
    return AgentModelResult(response=bridge.last_response, messages=tuple(result.all_messages()))
