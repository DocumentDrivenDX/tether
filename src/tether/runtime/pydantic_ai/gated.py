"""Fixed serial Agent composition; Tether callbacks own every execution permission."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import JsonValue
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, RunContext, Tool
from pydantic_ai.exceptions import ApprovalRequired, ToolFailed, UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tool_manager import ToolManager

from tether.ports import ModelPort
from tether.types import (
    Message,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ToolCall,
    ToolSpec,
)

from .bridge import PortModel, PrepareModel
from .messages import to_sdk_messages


class GatedAgent:
    """Run-local derived SDK state. Never a transcript, approval, or commit authority."""

    def __init__(
        self,
        model: ModelPort,
        request: ModelRequest,
        limits: ModelExecutionLimits,
        *,
        prepare: PrepareModel,
        accept_response: Callable[[ModelResponse], Awaitable[None]],
        execute: Callable[[ToolCall], Awaitable[Message | None]],
        deferred_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._execute = execute
        self._bridge = PortModel(model, request, limits, prepare, accept_response)
        self._agent: Agent[None, str | DeferredToolRequests] = Agent(
            deps_type=type(None),
            model=self._bridge,
            tools=[self._tool(t) for t in request.tools],
            output_type=[str, DeferredToolRequests],
            retries=0,
        )
        self._history: list[ModelMessage] = to_sdk_messages(request.messages, request.system)
        self._deferred: set[str] = set(deferred_ids)

    def _tool(self, spec: ToolSpec) -> Tool[None]:
        async def execute(ctx: RunContext[None], **arguments: JsonValue) -> str:
            call = ToolCall(id=ctx.tool_call_id or "", name=spec.name, arguments=arguments)
            message = await self._execute(call)
            if message is None:
                raise ApprovalRequired()
            if message.is_error:
                raise ToolFailed(message.content)
            return message.content

        return Tool.from_schema(
            execute,
            name=spec.name,
            description=spec.description,
            json_schema=spec.input_schema,
            takes_ctx=True,
            sequential=True,
        )

    async def run(self, *, continuing: bool = False) -> bool:
        """Return whether Agent stopped for deferred calls; no owned model/tool loop."""
        results = (
            DeferredToolResults(approvals={key: True for key in self._deferred})
            if continuing
            else None
        )
        try:
            with ToolManager.parallel_execution_mode("sequential"):
                result = await self._agent.run(
                    None,
                    message_history=self._history,
                    deferred_tool_results=results,
                    infer_name=False,
                )
        except UnexpectedModelBehavior:
            observed = self._bridge.last_response
            reason = (
                "empty_response"
                if observed and not observed.content and not observed.tool_calls
                else "invalid_response"
            )
            raise ModelUnavailable(reason) from None
        self._history = result.all_messages()
        if isinstance(result.output, DeferredToolRequests):
            self._deferred = {c.tool_call_id for c in result.output.approvals}
            return True
        self._deferred = set()
        return False
