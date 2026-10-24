"""Execution facade for the slice 2 subset. Agent exclusively owns iteration."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from tether.ports import ModelPort
from tether.types import (
    AttemptRecorder,
    Message,
    ModelExecutionLimits,
    ModelRequest,
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
from tether.types.history import HistoryProvider
from tether.types.tooling import tool_spec

from .governance import AllowAll, Governance, utc_now
from .reconstruction import pinned_spec, requested_turn, restore_pending, restored_usage
from .run import Run
from .tools import Invocation, Registry, ToolExecutor
from .workers import Workers


class Engine:
    """Slice 2 facade with process-local pending runs; durable continuation is slice 3."""

    def __init__(
        self,
        *,
        model: ModelPort,
        history: MessageHistory,
        model_limits: ModelExecutionLimits,
        attempt_factory: Callable[[RequestTags], AttemptRecorder] | None = None,
        history_provider: HistoryProvider | None = None,
        tools: tuple[Tool, ...] = (),
        providers: tuple[Provider, ...] = (),
        guards: tuple[ResultGuard, ...] = (),
        policy: Policy | None = None,
        limits: ExecutionLimits | None = None,
        clock: Clock = utc_now,
        max_output_tokens: int = 1024,
        specs: tuple[AgentSpec, ...] = (),
    ) -> None:
        self.model, self.history, self.model_limits = model, history, model_limits
        self.history_provider = history_provider
        self.attempt_factory, self.max_output_tokens = attempt_factory, max_output_tokens
        self.registry, self.guards = Registry(tools, providers), guards
        self.policy, self.limits, self.clock = (
            policy or AllowAll(),
            limits or ExecutionLimits(),
            clock,
        )
        self.workers = Workers(self.limits.max_workers)
        self._runs: dict[str, Run] = {}
        self._specs = {digest(s.model_dump(mode="json")): s.model_copy(deep=True) for s in specs}
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
            await self._supersede(actor, log)
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
                        facts={
                            "spec_digest": digest(spec.model_dump(mode="json")),
                            "spec_id": spec.id,
                            "spec_version": spec.version,
                        },
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
                requested = next((e for e in entries if e.key == "pending:" + inv_id), None)
                if requested is not None and "approval:" + inv_id not in keys:
                    await gov.fact(
                        "confirmation",
                        key="approval:" + inv_id,
                        state="superseded",
                        binding=requested.facts.get("binding"),
                    )
                started = "intent:" + inv_id in keys
                reason = (
                    "outcome_unknown"
                    if started and manifest.effect == "effectful"
                    else "superseded"
                    if requested is not None
                    else "interrupted"
                )
                await executor.result(
                    Invocation(source, manifest, inv_id),
                    "interrupted" if started else "denied",
                    reason,
                )

    def _create(self, gov: Governance, spec: AgentSpec) -> Run:
        self._specs[digest(spec.model_dump(mode="json"))] = spec.model_copy(deep=True)
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
        return Run(
            gov,
            executor,
            template,
            self.model,
            self.model_limits,
            self.attempt_factory,
            self.history_provider,
        )

    async def _supersede(self, actor: Actor, log: TurnLog) -> None:
        previous = self._runs.get(log.session.id)
        if previous is None:
            return
        previous.gov.actor, previous.gov.log, previous.gov.policy = actor, log, self.policy
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
        if run is None:
            run = await self._restore(actor, log, invocation_id)
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

    async def _restore(self, actor: Actor, log: TurnLog, invocation_id: str) -> Run | None:
        source_turn = requested_turn(log.entries, invocation_id)
        if source_turn is None:
            return None
        spec = pinned_spec(log.entries, self._specs)
        gov = Governance(
            actor, log, self.history, self.policy, source_turn, self.limits, self.clock
        )
        run = self._create(gov, spec)
        gov.usage = restored_usage(log.entries, source_turn)
        run.steps = gov.usage.steps
        if not await restore_pending(run, invocation_id):
            return None
        self._runs[log.session.id] = run
        return run

    async def aclose(self) -> bool:
        """Bounded shutdown; False explicitly means active work has not exited."""
        drained = await self.workers.close(self.limits.shutdown_timeout_seconds)
        return drained and not self._running
