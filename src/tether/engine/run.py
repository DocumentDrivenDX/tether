"""Execution facade for the slice 2 subset. Agent exclusively owns iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from tether.ports import ModelPort
from tether.runtime.pydantic_ai.attachments import projected
from tether.runtime.pydantic_ai.gated import GatedAgent
from tether.runtime.pydantic_ai.response import IncompleteModelResponse
from tether.types import (
    AttemptIdentity,
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
from tether.types.context import CODES, ContextError
from tether.types.execution import (
    ExecutionDenied,
    NewEntry,
    TurnResult,
)
from tether.types.history import HistoryProvider

from .attempts import JournalAttemptRecorder
from .context import ContextBoundary
from .context.attempts import CheckedRecorder
from .context.services import ContextServices
from .context.step import ContextStep
from .governance import Governance
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
        history_provider: HistoryProvider | None,
        context: ContextBoundary,
        live_text: Callable[[str, AttemptIdentity, int, str], Awaitable[None]] | None = None,
        live_done: Callable[
            [str, AttemptIdentity, Literal["outage", "cancelled", "reconnect"] | None],
            Awaitable[None],
        ]
        | None = None,
    ) -> None:
        self.gov, self.executor, self.template = gov, executor, template
        self.model, self.limits = model, limits
        self.runner: GatedAgent | None = None
        self.steps = 0
        self.result_turn_ids = frozenset({gov.turn_id})
        self.attempt_factory = attempt_factory
        self.history_provider = history_provider
        self.context = context
        self.live_text = live_text
        self.live_done = live_done
        self.live_attempts: dict[str, AttemptIdentity] = {}
        self.context_services = ContextServices(model, limits, history_provider, attempt_factory)
        self.preparation: ContextStep | None = None
        self.recorder: AttemptRecorder | None = None
        self.current_call: str | None = None
        self._incomplete: tuple[str | None, str, int, int] | None = None
        self.deferred_ids: frozenset[str] = frozenset()
        self.recovery_tools: tuple[ToolSpec, ...] = ()

    async def messages(self) -> tuple[Message, ...]:
        return await self.gov.messages()

    async def prepare(self, request: ModelRequest) -> ModelRequest:
        self._incomplete = None
        if self.executor.workers.closed:
            raise ExecutionDenied("engine_closed")
        if self.steps >= self.executor.spec.max_steps:
            raise ExecutionDenied("step_limit")
        await self.gov.authorize("call_model", "model:" + request.model)
        if self.preparation is None or self.preparation.used:
            self.preparation = ContextStep(
                self.context,
                self.gov,
                self.executor,
                request.model,
                self.result_turn_ids,
                self.context_services,
            )
        step = self.preparation
        async with step.guarded():
            originals = await step.capture()
            if (
                request.messages != projected(originals)
                or request.system != self.executor.spec.instructions
            ):
                raise ExecutionDenied("derived_history_changed")
            visible = {t.name for t in self.template.tools}
            self.steps += 1
            self.current_call = str(uuid4())
            tags = request.tags.model_copy(
                update={
                    "model_call_id": self.current_call,
                    "principal": self.gov.actor.principal or self.gov.actor.id,
                    "context": self.gov.log.session.held,
                }
            )
            request = request.model_copy(
                update={
                    "tools": tuple(
                        sorted(
                            (t for t in request.tools if t.name in visible), key=lambda t: t.name
                        )
                    ),
                    "tags": tags,
                    "messages": originals,
                }
            )
            request = await step.prepare(request)
            await self.gov.authorize("call_model", "model:" + request.model)
            self.recorder = (
                CheckedRecorder(self.attempt_factory(tags), step, self.gov, tags)
                if self.attempt_factory is not None
                else JournalAttemptRecorder(
                    self.gov,
                    tags,
                    request.model,
                    self.history_provider,
                    step.recheck,
                    admit=step.check,
                    live_text=self._live_delta if self.live_text is not None else None,
                )
            )
            return request.model_copy(
                update={"attempts": self.recorder, "credential": self.gov.actor.credential}
            )

    async def accept(self, response: ModelResponse) -> None:
        incomplete = response.finish_reason in {"length", "filtered", "unknown"}
        calls = () if incomplete else response.tool_calls
        message = Message(role="assistant", content=response.content, tool_calls=calls)
        ref = await self.gov.history.put(
            actor=self.gov.actor,
            session_id=self.gov.log.session.id,
            key="model:" + str(self.current_call),
            message=message,
        )
        manifests = tuple(self.executor.registry.manifest(c, i) for i, c in enumerate(calls))
        accepted = await self.gov.commit(
            [
                NewEntry(
                    kind="message",
                    key="source:" + str(self.current_call),
                    turn_id=self.gov.turn_id,
                    history_ref=ref,
                    call_manifest=manifests,
                    facts={
                        "role": "assistant",
                        "is_error": False,
                        "model_status": "incomplete" if incomplete else "ok",
                    },
                ),
                NewEntry(
                    kind="model_call",
                    turn_id=self.gov.turn_id,
                    facts={
                        "status": "incomplete" if incomplete else "ok",
                        "model_call_id": self.current_call,
                        "purpose": "answer",
                        **self.attempt_facts(),
                        "finish_reason": response.finish_reason,
                        "usage": response.usage.model_dump(mode="json") if response.usage else None,
                    },
                ),
            ]
        )
        if incomplete:
            self._incomplete = (
                self.current_call,
                response.finish_reason,
                accepted[0].index,
                accepted[1].index,
            )
        else:
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
                self.preparation = ContextStep(
                    self.context,
                    self.gov,
                    self.executor,
                    self.template.model,
                    self.result_turn_ids,
                    self.context_services,
                )
                async with self.preparation.guarded():
                    messages = await self.preparation.capture()
                    request = self.template.model_copy(update={"messages": messages})
                self.runner = GatedAgent(
                    self.model,
                    request,
                    self.limits,
                    prepare=self.prepare,
                    accept_response=self.accept,
                    execute=self.executor.execute,
                    deferred_ids=self.deferred_ids,
                    recovery_tools=self.recovery_tools,
                    observe_model=lambda request: self.gov.observers.model(
                        request,
                        owner=self.gov.log.session.owner,
                        usage=lambda call: self.gov.accounting.call_usage(call),
                    ),
                )
            waiting, incomplete = (
                (False, None) if recovered_final else await self.outcome(continuing)
            )
            visible = await self.visible_messages()
            stop, reason = (
                ("model_incomplete", incomplete)
                if incomplete is not None
                else ("confirmation_required" if waiting else "final", None)
            )
        except ContextError as error:
            stop, reason = (
                "context_unavailable",
                error.code if error.code in CODES else "context_failed",
            )
            await self.gov.fact(
                "x.context.failed",
                reason=reason,
                preparation_ordinal=self.preparation.ordinal
                if self.preparation is not None
                else None,
            )
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
            await asyncio.shield(self.finish_live("cancelled"))
            await asyncio.shield(self.gov.fact("turn_ended", stop_reason="cancelled"))
            raise
        reset = "outage" if stop == "model_unavailable" else None
        await self.finish_live(reset)
        await self.gov.fact("turn_ended", stop_reason=stop, reason=reason)
        return TurnResult(
            turn_id=self.gov.turn_id,
            stop_reason=stop,
            reason=reason,
            pending=tuple(self.executor.pending.values()) if visible else (),
            messages=visible,
        )

    async def _live_delta(
        self, owner: str, identity: AttemptIdentity, offset: int, text: str
    ) -> None:
        self.live_attempts[identity.attempt_id] = identity.model_copy(deep=True)
        if self.live_text is not None:
            await self.live_text(owner, identity, offset, text)

    async def finish_live(self, reason: Literal["outage", "cancelled", "reconnect"] | None) -> None:
        if self.live_done is None:
            return
        for identity in tuple(self.live_attempts.values()):
            await self.live_done(self.gov.log.session.owner, identity, reason)
        self.live_attempts.clear()

    async def outcome(self, continuing: bool) -> tuple[bool, str | None]:
        try:
            return await self._run(continuing), None
        except IncompleteModelResponse as error:
            accepted = self._incomplete
            if accepted is None or accepted[:2] != (self.current_call, error.reason):
                raise ModelUnavailable("invalid_terminal") from None
            return False, accepted[1]

    async def visible_messages(self) -> tuple[Message, ...]:
        return tuple(
            [
                await self.gov.read(e.history_ref)
                for e in self.gov.log.entries
                if e.kind == "message" and e.turn_id in self.result_turn_ids
            ]
        )

    async def _run(self, continuing: bool) -> bool:
        if self.runner is None:
            raise ExecutionDenied("missing_runner")
        return await self.runner.run(continuing=continuing)
