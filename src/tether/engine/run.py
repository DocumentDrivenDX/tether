"""Execution facade for the slice 2 subset. Agent exclusively owns iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import uuid4

from tether.ports import ModelPort
from tether.runtime.pydantic_ai.gated import GatedAgent
from tether.types import (
    AttemptRecorder,
    JsonValue,
    Message,
    ModelAttemptDenied,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    RequestTags,
    ToolSpec,
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
        self.result_turn_ids = frozenset({gov.turn_id})
        self.attempt_factory = attempt_factory
        self.history_provider = history_provider
        self.recorder: AttemptRecorder | None = None
        self.current_call: str | None = None
        self.deferred_ids: frozenset[str] = frozenset()
        self.recovery_tools: tuple[ToolSpec, ...] = ()

    async def messages(self) -> tuple[Message, ...]:
        return await self.gov.messages()

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
        visible = {t.name for t in self.template.tools}
        request = request.model_copy(
            update={"tools": tuple(t for t in request.tools if t.name in visible)}
        )
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
        tags = request.tags.model_copy(
            update={
                "model_call_id": self.current_call,
                "principal": self.gov.actor.principal or self.gov.actor.id,
                "context": self.gov.log.session.held,
            }
        )
        self.recorder = (
            self.attempt_factory(tags)
            if self.attempt_factory is not None
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
                        **self.attempt_facts(),
                        "finish_reason": response.finish_reason,
                        "usage": response.usage.model_dump(mode="json") if response.usage else None,
                    },
                ),
            ]
        )
        self.executor.bind(accepted[0])

    def attempt_facts(self) -> dict[str, JsonValue]:
        recorder = self.recorder
        journal = isinstance(recorder, JournalAttemptRecorder)
        return {
            "attempts_complete": journal,
            "attempt_ids": list(recorder.started) if journal else [],
            "exchange_ref": recorder.latest_exchange.model_dump(mode="json")
            if journal and recorder.latest_exchange is not None
            else None,
        }

    async def finish(
        self, continuing: bool = False, *, recovered_final: bool = False
    ) -> TurnResult:
        visible: tuple[Message, ...] = ()
        try:
            await self.gov.authorize("read_session", self.gov.log.session.id)
            if self.runner is None and not recovered_final:
                request = self.template.model_copy(update={"messages": await self.messages()})
                self.runner = GatedAgent(
                    self.model,
                    request,
                    self.limits,
                    prepare=self.prepare,
                    accept_response=self.accept,
                    execute=self.executor.execute,
                    deferred_ids=self.deferred_ids,
                    recovery_tools=self.recovery_tools,
                )
            waiting = False if recovered_final else await self._run(continuing)
            visible = tuple(
                [
                    await self.gov.read(e.history_ref)
                    for e in self.gov.log.entries
                    if e.kind == "message" and e.turn_id in self.result_turn_ids
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
            await self.gov.commit(
                [
                    NewEntry(
                        kind="model_call",
                        turn_id=self.gov.turn_id,
                        facts={
                            "status": "error",
                            "reason": reason,
                            "model_call_id": self.current_call,
                            "purpose": "answer",
                            **self.attempt_facts(),
                        },
                    )
                ]
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

    async def _run(self, continuing: bool) -> bool:
        if self.runner is None:
            raise ExecutionDenied("missing_runner")
        return await self.runner.run(continuing=continuing)
