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
from tether.types.canonical import digest
from tether.types.execution import (
    Actor,
    AgentSpec,
    Answer,
    ApprovalBinding,
    Clock,
    ExecutionDenied,
    ExecutionLimits,
    MessageHistory,
    NewEntry,
    Policy,
    Provider,
    ResultGuard,
    Tool,
    TurnLog,
    TurnResult,
)
from tether.types.tooling import tool_spec

from .governance import AllowAll, Governance, utc_now
from .providers import narrow_request
from .tools import Invocation, Registry, ToolExecutor
from .workers import Workers


class Run:
    def __init__(
        self,
        gov: Governance,
        executor: ToolExecutor,
        template: ModelRequest,
        model: ModelPort,
        limits: ModelExecutionLimits,
        attempt_factory: Callable[[RequestTags], AttemptRecorder],
    ) -> None:
        self.gov, self.executor, self.template = gov, executor, template
        self.model, self.limits = model, limits
        self.runner: GatedAgent | None = None
        self.steps = 0
        self.attempt_factory = attempt_factory
        self.current_call: str | None = None

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
        return request.model_copy(
            update={
                "tags": tags,
                "attempts": self.attempt_factory(tags),
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
                "usage_complete": usage.usage_complete and response.usage is not None,
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


class Engine:
    """Slice 2 facade with process-local pending runs; durable continuation is slice 3."""

    def __init__(
        self,
        *,
        model: ModelPort,
        history: MessageHistory,
        model_limits: ModelExecutionLimits,
        attempt_factory: Callable[[RequestTags], AttemptRecorder],
        tools: tuple[Tool, ...] = (),
        providers: tuple[Provider, ...] = (),
        guards: tuple[ResultGuard, ...] = (),
        policy: Policy | None = None,
        limits: ExecutionLimits | None = None,
        clock: Clock = utc_now,
        max_output_tokens: int = 1024,
    ) -> None:
        self.model, self.history, self.model_limits = model, history, model_limits
        self.attempt_factory, self.max_output_tokens = attempt_factory, max_output_tokens
        self.registry, self.guards = Registry(tools, providers), guards
        self.policy, self.limits, self.clock = (
            policy or AllowAll(),
            limits or ExecutionLimits(),
            clock,
        )
        self.workers = Workers(self.limits.max_workers)
        self._runs: dict[str, Run] = {}
        self._running: set[str] = set()

    def _enter(self, actor: Actor, log: TurnLog) -> None:
        if self.workers.closed:
            raise ExecutionDenied("engine_closed")
        if actor.id != log.session.owner:
            raise ExecutionDenied("denied")
        if log.session.id in self._running or self.workers.busy(log.session.id):
            raise ExecutionDenied("worker_outstanding")
        self._running.add(log.session.id)

    async def run_turn(
        self, actor: Actor, log: TurnLog, text: str, *, spec: AgentSpec
    ) -> TurnResult:
        self._enter(actor, log)
        try:
            await self._supersede(log.session.id)
            gov = Governance(
                actor, log, self.history, self.policy, str(uuid4()), self.limits, self.clock
            )
            await gov.authorize("read_session", log.session.id)
            await self._close_incomplete(gov, spec)
            ref = await self.history.put(
                actor=actor,
                session_id=log.session.id,
                key="user:" + gov.turn_id,
                message=Message(role="user", content=text),
            )
            await gov.commit(
                [
                    NewEntry(
                        kind="turn_started",
                        turn_id=gov.turn_id,
                        facts={"spec_digest": digest(spec.model_dump(mode="json"))},
                    ),
                    NewEntry(kind="message", turn_id=gov.turn_id, history_ref=ref),
                ]
            )
            run = self._create(gov, spec)
            self._runs[log.session.id] = run
            return await run.finish()
        finally:
            self._running.discard(log.session.id)

    async def _close_incomplete(self, gov: Governance, spec: AgentSpec) -> None:
        executor = ToolExecutor(gov, self.registry, spec, self.workers, self.guards)
        entries = gov.log.entries
        keys = {e.key for e in entries}
        for source in entries:
            for manifest in source.call_manifest:
                inv_id = digest([gov.log.session.id, source.index, manifest.position])
                if "result:" + inv_id in keys:
                    continue
                started = "intent:" + inv_id in keys
                reason = (
                    "outcome_unknown"
                    if started and manifest.effect == "effectful"
                    else "interrupted"
                )
                await executor.result(
                    Invocation(source, manifest, inv_id),
                    "interrupted" if started else "denied",
                    reason,
                )

    def _create(self, gov: Governance, spec: AgentSpec) -> Run:
        selected = tuple(
            tool_spec(p.tool)
            for name, p in self.registry.tools.items()
            if name in spec.tools and p.tool.model_visible
        )
        tags = RequestTags(
            session_id=gov.log.session.id,
            principal=gov.actor.id,
            turn_id=gov.turn_id,
            model_call_id=str(uuid4()),
            spec_id=spec.id,
            spec_version=spec.version,
            context=gov.log.session.held,
        )
        template = ModelRequest(
            model=spec.model,
            tags=tags,
            system=spec.instructions,
            messages=(),
            tools=selected,
            max_output_tokens=self.max_output_tokens,
            credential=gov.actor.credential,
        )
        executor = ToolExecutor(gov, self.registry, spec, self.workers, self.guards)
        return Run(gov, executor, template, self.model, self.model_limits, self.attempt_factory)

    async def _supersede(self, session_id: str) -> None:
        previous = self._runs.get(session_id)
        if previous is None:
            return
        for inv_id in tuple(previous.executor.pending):
            pending = previous.executor.pending[inv_id]
            await previous.gov.fact(
                "confirmation",
                key="approval:" + inv_id,
                state="superseded",
                binding=ApprovalBinding.model_validate(
                    pending.model_dump(include=set(ApprovalBinding.model_fields))
                ).model_dump(mode="json"),
            )
            inv = next(i for i in previous.executor.calls.values() if i.id == inv_id)
            await previous.executor.result(inv, "denied", "superseded")

    async def answer(
        self, actor: Actor, log: TurnLog, invocation_id: str, answer: Answer
    ) -> TurnResult | None:
        run = self._runs.get(log.session.id)
        if run is None or invocation_id not in run.executor.pending:
            return None
        if log.session.id in self._running:
            return None
        self._enter(actor, log)
        try:
            run.gov.actor, run.gov.log, run.gov.policy = actor, log, self.policy
            pending = run.executor.pending[invocation_id]
            await run.gov.authorize("approve", invocation_id)
            state = "approved" if answer.approved else "declined"
            if pending.expires_at and self.clock() > pending.expires_at:
                state = "expired"
            binding = pending.model_dump(include=set(ApprovalBinding.model_fields), mode="json")
            await run.gov.fact(
                "confirmation", key="approval:" + invocation_id, state=state, binding=binding
            )
            run.executor.pending.pop(invocation_id)
            if run.executor.pending:
                return TurnResult(
                    turn_id=run.gov.turn_id,
                    stop_reason="confirmation_required",
                    pending=tuple(run.executor.pending.values()),
                )
            return await run.finish(continuing=True)
        finally:
            self._running.discard(log.session.id)

    async def aclose(self) -> bool:
        """Bounded shutdown; False explicitly means active work has not exited."""
        drained = await self.workers.close(self.limits.shutdown_timeout_seconds)
        return drained and not self._running
