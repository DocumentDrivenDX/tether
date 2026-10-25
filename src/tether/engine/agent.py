"""Gated execution and journal-derived continuation; Agent exclusively owns iteration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from uuid import uuid4

from tether.ports import ModelPort
from tether.types import (
    AttemptIdentity,
    AttemptRecorder,
    Message,
    ModelExecutionLimits,
    ModelRequest,
    RequestTags,
)
from tether.types.canonical import digest
from tether.types.context import ContextConfig, ContextStrategy, TokenEstimator
from tether.types.execution import (
    Actor,
    AgentSpec,
    Answer,
    AuthzRequest,
    Clock,
    Decision,
    Entry,
    ExecutionDenied,
    ExecutionLimits,
    MessageHistory,
    PendingConfirmation,
    Policy,
    Provider,
    Resolution,
    ResultGuard,
    SessionState,
    Tool,
    TurnLog,
    TurnResult,
    TurnStart,
    WorkerOutstanding,
    copy_actor,
)
from tether.types.history import HistoryProvider
from tether.types.lifetime import WorkerLifetime
from tether.types.telemetry import AuditSink, DeliveryStats, TelemetryLimits, Tracer
from tether.types.tooling import tool_spec

from . import resuming
from .closing import close_session
from .confirmations import approval_entries
from .context import ContextBoundary
from .governance import AllowAll, Governance, utc_now
from .inspection import describe_pending
from .observers.facade import Observers
from .reconstruction import pinned_spec, requested_turn, restore_pending
from .recovery import resume_chain
from .resolution import resolve
from .run import Run
from .starts import accept_input
from .tools import Registry, ToolExecutor
from .workers import Workers


class Engine:
    """Agent facade whose cached runs are reconstructable from accepted journal/history."""

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
        context_config: ContextConfig | None = None,
        estimator: TokenEstimator | None = None,
        context: ContextStrategy | None = None,
        specs: tuple[AgentSpec, ...] = (),
        audit: AuditSink | None = None,
        tracer: Tracer | None = None,
        telemetry_limits: TelemetryLimits | None = None,
        live_text: Callable[[AttemptIdentity, int, str], Awaitable[None]] | None = None,
    ) -> None:
        self.model, self.history, self.model_limits = model, history, model_limits
        self.history_provider = history_provider
        self.attempt_factory = attempt_factory
        self.context = ContextBoundary(context_config, estimator, context)
        self.registry, self.guards = Registry(tools, providers), guards
        self.policy, self.limits, self.clock = (
            policy if policy is not None else AllowAll(),
            limits if limits is not None else ExecutionLimits(),
            clock,
        )
        self.observers = Observers(audit, tracer, telemetry_limits)
        self.live_text = live_text
        self.observers.steps.register(self.registry.providers)
        self.workers = Workers(
            self.limits.max_workers, operation_capacity=self.limits.max_worker_operations
        )
        self._runs: dict[str, Run] = {}
        self._specs = {digest(s.model_dump(mode="json")): s.model_copy(deep=True) for s in specs}
        self._running: set[str] = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        if current is not self._loop:
            raise RuntimeError("wrong_event_loop")
        self.observers.delivery.bind()

    def _enter(self, actor: Actor, log: TurnLog) -> None:
        self._bind_loop()
        if self.workers.closed:
            raise ExecutionDenied("engine_closed")
        if actor.id != log.session.owner:
            raise ExecutionDenied("denied")
        self.workers.scopes[log.session.id] = log.workers
        if log.session.id in self._running or self.workers.busy(log.session.id):
            raise WorkerOutstanding("worker_outstanding")
        self._running.add(log.session.id)
        self._idle.clear()

    def _governance(self, actor: Actor, log: TurnLog, turn_id: str) -> Governance:
        return Governance(
            actor,
            log,
            self.history,
            self.policy,
            turn_id,
            self.limits,
            self.clock,
            observers=self.observers,
        )

    @contextmanager
    def _operation(self, actor: Actor, log: TurnLog) -> Generator[None, None, None]:
        self._enter(actor, log)
        session_id = log.session.id
        try:
            with self.observers.tracer.start_as_current_span(
                "tether.turn",
                attributes={
                    "tether.owner": log.session.owner,
                    "tether.session_id": log.session.id,
                    "tether.principal": actor.principal or actor.id,
                },
            ):
                yield
        finally:
            self._running.discard(session_id)
            if not self._running:
                self._idle.set()

    @property
    def audit_stats(self) -> DeliveryStats:
        return self.observers.delivery.stats

    @property
    def observation_failures(self) -> int:
        return self.observers.failures + self.observers.tracer.failures

    async def wait_observers_closed(self) -> None:
        """Wait separately from physical cleanup; this never owns session storage."""
        await self.observers.delivery.wait_closed()

    async def run_turn(
        self,
        actor: Actor,
        log: TurnLog,
        text: str,
        *,
        spec: AgentSpec,
        start: TurnStart | None = None,
        accepted: Callable[[], None] | None = None,
    ) -> TurnResult:
        with self._operation(actor, log):
            spec = spec.model_copy(deep=True)
            start = (start or TurnStart(turn_id=log.turn_id or str(uuid4()))).model_copy(deep=True)
            gov = self._governance(actor, log, start.turn_id)
            gov.spec_id, gov.spec_version = spec.id, spec.version
            await accept_input(gov, spec, text, start)
            self._runs.pop(log.session.id, None)
            if accepted is not None:
                accepted()
            run = self._create(gov, spec)
            self._runs[log.session.id] = run
            return await run.finish()

    def spec(self, spec_id: str) -> AgentSpec:
        values = [value for value in self._specs.values() if value.id == spec_id]
        if len(values) != 1:
            raise ExecutionDenied("spec_unavailable")
        return values[0].model_copy(deep=True)

    def _create(self, gov: Governance, spec: AgentSpec) -> Run:
        gov.spec_id, gov.spec_version = spec.id, spec.version
        self._specs[digest(spec.model_dump(mode="json"))] = spec.model_copy(deep=True)
        selected = tuple(
            tool_spec(p.tool)
            for name, p in self.registry.tools.items()
            if name in spec.tools and p.tool.model_visible
        )
        tags = RequestTags(
            session_id=gov.log.session.id,
            principal=gov.actor.principal or gov.actor.id,
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
            max_output_tokens=self.context.output_tokens(spec.model),
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
            self.context,
            self.live_text,
        )

    async def answer(
        self,
        actor: Actor,
        log: TurnLog,
        invocation_id: str,
        answer: Answer,
        *,
        accepted: Callable[[], None] | None = None,
    ) -> TurnResult | None:
        self._bind_loop()
        run = await self._pending_run(actor, log, invocation_id)
        if run is None or invocation_id not in run.executor.pending:
            return None
        if log.session.id in self._running:
            return None
        with self._operation(actor, log):
            run.gov.actor, run.gov.log, run.gov.policy = actor, log, self.policy
            pending = run.executor.pending[invocation_id]
            await run.gov.authorize("approve", invocation_id)
            state = "approved" if answer.approved else "declined"
            if pending.expires_at and self.clock() > pending.expires_at:
                state = "expired"
            entries = await approval_entries(
                run.gov, pending, state, answer.note, resuming=len(run.executor.pending) == 1
            )
            await run.gov.commit(entries)
            run.executor.pending.pop(invocation_id)
            if accepted is not None:
                accepted()
            if run.executor.pending:
                return TurnResult(
                    turn_id=run.gov.turn_id,
                    stop_reason="confirmation_required",
                    pending=tuple(run.executor.pending.values()),
                )
            return await run.finish(continuing=True)

    async def _pending_run(self, actor: Actor, log: TurnLog, invocation_id: str) -> Run | None:
        source_turn = requested_turn(log.entries, invocation_id)
        if source_turn is None:
            return None
        run = self._runs.get(log.session.id)
        if run is not None and run.gov.turn_id == source_turn:
            return run
        return await self._restore(actor, log, invocation_id)

    async def _restore(self, actor: Actor, log: TurnLog, invocation_id: str) -> Run | None:
        self.workers.scopes[log.session.id] = log.workers
        if self.workers.busy(log.session.id):
            raise WorkerOutstanding("worker_outstanding")
        source_turn = requested_turn(log.entries, invocation_id)
        if source_turn is None:
            return None
        spec = pinned_spec(log.entries, self._specs)
        gov = self._governance(actor, log, source_turn)
        run = self._create(gov, spec)
        chain = frozenset(resume_chain(log.entries, source_turn))
        gov.configure_usage(chain)
        run.result_turn_ids = chain
        run.steps = gov.usage.steps
        if not await restore_pending(run, invocation_id):
            return None
        self._runs[log.session.id] = run
        return run

    async def inspect_pending(
        self,
        actor: Actor,
        session: SessionState,
        source: Entry,
        request: Entry,
        proposal: Message,
        *,
        lifetime: WorkerLifetime,
    ) -> PendingConfirmation:
        self._bind_loop()
        try:
            async with asyncio.timeout(self.limits.hook_timeout_seconds):
                decision = Decision.model_validate(
                    await self.policy.authorize(
                        AuthzRequest(
                            actor=copy_actor(actor), action="read_session", resource=session.id
                        )
                    )
                )
        except Exception:
            raise ExecutionDenied("policy_denied") from None
        if not decision.allowed or actor.id != session.owner:
            raise ExecutionDenied("policy_denied")
        self.workers.scopes[session.id] = lifetime
        return await describe_pending(
            actor,
            session,
            source,
            request,
            proposal,
            self.registry,
            self._specs,
            self.workers,
            self.limits,
        )

    async def resume(
        self,
        actor: Actor,
        log: TurnLog,
        *,
        accepted: Callable[[], None] | None = None,
    ) -> TurnResult:
        with self._operation(actor, log):
            gov = self._governance(copy_actor(actor), log, log.turn_id or str(uuid4()))
            await resuming.decisions(gov)
            plan = await resuming.planned(gov, self.registry, self.workers)
            spec = pinned_spec(log.entries, self._specs)
            await resuming.accept(gov, plan, spec, self.registry, self.workers)
            self._runs.pop(log.session.id, None)
            if accepted is not None:
                accepted()
            run = self._create(gov, spec)
            await resuming.restore(run, plan)
            self._runs[log.session.id] = run
            return await run.finish(
                continuing=bool(run.deferred_ids), recovered_final=not plan.steps
            )

    async def close_session(self, actor: Actor, log: TurnLog) -> None:
        with self._operation(actor, log):
            gov = self._governance(actor, log, log.turn_id or str(uuid4()))
            await close_session(gov)
            self._runs.pop(log.session.id, None)

    async def resolve(self, actor: Actor, log: TurnLog, value: Resolution) -> None:
        with self._operation(actor, log):
            gov = self._governance(actor, log, log.turn_id or str(uuid4()))
            await resolve(gov, value.model_copy(deep=True))
            self._runs.pop(log.session.id, None)

    async def wait_closed(self) -> None:
        """Wait for actual exits after aclose sealed submissions; caller may bound this wait."""
        self._bind_loop()
        if not self.workers.closed:
            raise RuntimeError("engine_not_closing")
        await self._idle.wait()
        if self.workers.active:
            await asyncio.wait(tuple(self.workers.active))

    async def aclose_execution(self, *, deadline: float | None = None) -> bool:
        """Seal execution; observer delivery cannot delay physical drain or store release."""
        self._bind_loop()
        seconds = self.limits.shutdown_timeout_seconds
        if deadline is not None:
            seconds = max(0, min(seconds, deadline - asyncio.get_running_loop().time()))
        drained = await self.workers.close(seconds)
        return drained and not self._running

    async def aclose_observers(self, *, deadline: float | None = None) -> bool:
        self._bind_loop()
        return await self.observers.delivery.aclose(deadline=deadline)

    async def aclose(self) -> bool:
        """Bounded physical/observational waits sharing one absolute deadline."""
        self._bind_loop()
        deadline = asyncio.get_running_loop().time() + self.limits.shutdown_timeout_seconds
        try:
            drained = await self.aclose_execution(deadline=deadline)
        finally:
            observed = await self.aclose_observers(deadline=deadline)
        return drained and observed
