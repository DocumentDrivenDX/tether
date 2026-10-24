"""Execution facade for the slice 2 subset. Agent exclusively owns iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import uuid4

from tether.ports import ModelPort
from tether.runtime.pydantic_ai.gated import GatedAgent
from tether.types import (
    AttemptRecorder,
    Message,
    ModelAttemptDenied,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    RequestTags,
)
from tether.types.execution import (
    ExecutionDenied,
    NewEntry,
    TurnResult,
)
from tether.types.history import HistoryProvider

from .attempts import JournalAttemptRecorder
from .governance import Governance
from .providers import narrow_request
from .tools import ToolExecutor


class Run:
    def __init__(
        self,
        gov: Governance,
        executor: ToolExecutor,
        template: ModelRequest,
        model: ModelPort,
        limits: ModelExecutionLimits,
        attempt_factory: Callable[[RequestTags], AttemptRecorder] | None,
        history_provider: HistoryProvider | None = None,
    ) -> None:
        self.gov, self.executor, self.template = gov, executor, template
        self.model, self.limits = model, limits
        self.runner: GatedAgent | None = None
        self.steps = 0
        self.attempt_factory = attempt_factory
        self.history_provider = history_provider
        self.recorder: AttemptRecorder | None = None
        self.current_call: str | None = None
        self.deferred_ids: frozenset[str] = frozenset()

    async def messages(self) -> tuple[Message, ...]:
        return tuple(
            [
                await self.gov.read(e.history_ref)
                for e in self.gov.log.entries
                if e.kind == "message"
            ]
        )

    async def prepare(self, request: ModelRequest) -> ModelRequest:
        if self.executor.workers.closed:
            raise ExecutionDenied("engine_closed")
        if self.steps >= self.executor.spec.max_steps:
            raise ExecutionDenied("step_limit")
        await self.gov.authorize("call_model", "model:" + request.model)
        if (
            request.messages != await self.messages()
            or request.system != self.executor.spec.instructions
        ):
            raise ExecutionDenied("derived_history_changed")
        request = await narrow_request(
            self.gov,
            self.executor.workers,
            self.executor.registry.providers,
            self.executor.spec,
            request,
        )
        await self.gov.authorize("call_model", "model:" + request.model)
        self.steps += 1
        self.current_call = str(uuid4())
        tags = request.tags.model_copy(update={"model_call_id": self.current_call})
        self.recorder = (
            self.attempt_factory(tags)
            if self.attempt_factory
            else JournalAttemptRecorder(
                self.gov, tags, request.model, self.history_provider, self.messages
            )
        )
        return request.model_copy(
            update={
                "tags": tags,
                "attempts": self.recorder,
                "credential": self.gov.actor.credential,
            }
        )

    async def accept(self, response: ModelResponse) -> None:
        message = Message(
            role="assistant", content=response.content, tool_calls=response.tool_calls
        )
        ref = await self.gov.history.put(
            actor=self.gov.actor,
            session_id=self.gov.log.session.id,
            key="model:" + str(self.current_call),
            message=message,
        )
        manifests = tuple(
            self.executor.registry.manifest(c, i) for i, c in enumerate(response.tool_calls)
        )
        accepted = await self.gov.commit(
            [
                NewEntry(
                    kind="message",
                    key="source:" + str(self.current_call),
                    turn_id=self.gov.turn_id,
                    history_ref=ref,
                    call_manifest=manifests,
                    facts={"role": "assistant", "is_error": False},
                ),
                NewEntry(
                    kind="model_call",
                    turn_id=self.gov.turn_id,
                    facts={
                        "status": "ok",
                        "model_call_id": self.current_call,
                        "purpose": "answer",
                        "attempt_ids": list(self.recorder.started)
                        if isinstance(self.recorder, JournalAttemptRecorder)
                        else [],
                        "exchange_ref": self.recorder.latest_exchange.model_dump(mode="json")
                        if isinstance(self.recorder, JournalAttemptRecorder)
                        and self.recorder.latest_exchange
                        else None,
                        "finish_reason": response.finish_reason,
                        "usage": response.usage.model_dump(mode="json") if response.usage else None,
                    },
                ),
            ]
        )
        self.executor.bind(accepted[0])
        usage = self.gov.usage
        self.gov.usage = usage.model_copy(
            update={
                "steps": self.steps,
                "model_calls": usage.model_calls + 1,
                "input_tokens": usage.input_tokens
                + (response.usage.input_tokens if response.usage else 0),
                "output_tokens": usage.output_tokens
                + (response.usage.output_tokens if response.usage else 0),
                "usage_complete": (
                    self.recorder.baseline_usage_complete and len(self.recorder.started) == 1
                    if isinstance(self.recorder, JournalAttemptRecorder)
                    else usage.usage_complete
                )
                and response.usage is not None,
            }
        )

    async def finish(self, continuing: bool = False) -> TurnResult:
        visible: tuple[Message, ...] = ()
        try:
            await self.gov.authorize("read_session", self.gov.log.session.id)
            if self.runner is None:
                request = self.template.model_copy(update={"messages": await self.messages()})
                self.runner = GatedAgent(
                    self.model,
                    request,
                    self.limits,
                    prepare=self.prepare,
                    accept_response=self.accept,
                    execute=self.executor.execute,
                    deferred_ids=self.deferred_ids,
                )
            waiting = await self.runner.run(continuing=continuing)
            visible = tuple(
                [
                    await self.gov.read(e.history_ref)
                    for e in self.gov.log.entries
                    if e.kind == "message" and e.turn_id == self.gov.turn_id
                ]
            )
            stop, reason = ("confirmation_required" if waiting else "final"), None
        except ExecutionDenied as error:
            stop = error.reason if error.reason == "step_limit" else "model_denied"
            reason = error.reason
        except ModelAttemptDenied as error:
            stop, reason = "model_denied", error.reason
        except ModelUnavailable as error:
            stop, reason = "model_unavailable", error.reason
            await self.gov.fact(
                "model_call",
                status="error",
                reason=reason,
                model_call_id=self.current_call,
                purpose="answer",
            )
        except asyncio.CancelledError:
            await asyncio.shield(self.gov.fact("turn_ended", stop_reason="cancelled"))
            raise
        await self.gov.fact("turn_ended", stop_reason=stop, reason=reason)
        return TurnResult(
            turn_id=self.gov.turn_id,
            stop_reason=stop,
            reason=reason,
            pending=tuple(self.executor.pending.values()) if visible else (),
            messages=visible,
        )
