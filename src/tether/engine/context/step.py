"""One bounded preparation; accepted context use is checked before every physical attempt."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from tether.context.materialization import materialize, merged_parts
from tether.context.provenance import SourceProjection
from tether.context.validation import copied, validated_plan
from tether.types import Message, ModelRequest
from tether.types.canonical import canonical, digest
from tether.types.context import (
    CODES,
    CompactionRecord,
    ContextError,
    ContextGroup,
    ContextInput,
    ContextPlan,
    ContextPreparation,
    ContextSummary,
    ContextUse,
    ModelLimits,
    TokenEstimate,
)
from tether.types.execution import Entry

from ..governance import Governance
from ..providers import narrow_sources
from ..tools import ToolExecutor
from . import ContextBoundary
from .catalog import compatible
from .content import load
from .journal import next_ordinal
from .resolution import resolve
from .services import ContextServices
from .sources import Snapshot, capture, charge


def request_digest(request: ModelRequest) -> str:
    # Trace propagation is added by the observation scope after preparation; it is not prompt input.
    return digest(request.model_dump(mode="json", exclude={"traceparent"}))


class ContextStep:
    def __init__(
        self,
        boundary: ContextBoundary,
        gov: Governance,
        executor: ToolExecutor,
        tier: str,
        turns: frozenset[str],
        services: ContextServices,
    ) -> None:
        self.boundary, self.gov, self.executor, self.turns = boundary, gov, executor, turns
        self.config = boundary.configuration(tier)
        self.profile = self.config.limits[tier]
        self.tier = tier
        self.services = services
        self.summaries: tuple[ContextSummary, ...] = ()
        self.summary_texts: dict[int, str] = {}
        self.deadline = asyncio.get_running_loop().time() + self.config.timeout_seconds
        try:
            identity = ContextPlan(
                strategy_id=boundary.strategy.id,
                strategy_version=boundary.strategy.version,
                parts=(),
            )
        except (ValueError, TypeError, AttributeError):
            raise ContextError("context_invalid") from None
        except Exception:
            raise ContextError("context_failed") from None
        self.strategy_id, self.strategy_version = identity.strategy_id, identity.strategy_version
        self.original: Snapshot | None = None
        self.used = False
        self.accepted: Entry | None = None
        self.request: ModelRequest | None = None
        self.ordinal: int | None = None

    @asynccontextmanager
    async def guarded(self) -> AsyncGenerator[None]:
        timer = asyncio.timeout_at(self.deadline)
        try:
            async with timer:
                self._deadline()
                yield
                self._deadline()
        except TimeoutError:
            if timer.expired():
                raise ContextError("context_failed") from None
            raise

    def _deadline(self) -> None:
        if asyncio.get_running_loop().time() >= self.deadline:
            raise ContextError("context_failed")

    async def capture(self) -> tuple[Message, ...]:
        self.original = await capture(self.gov, self.config.max_source_bytes, self.turns)
        return tuple(s.message.model_copy(deep=True) for s in self.original.sources)

    def scope_digest(self) -> str:
        actor, session = self.gov.actor, self.gov.log.session
        return digest(
            {
                "owner": session.owner,
                "actor": actor.id,
                "principal": actor.principal or actor.id,
                "held": {key: list(value) for key, value in session.held.items()},
            }
        )

    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        estimator = self.boundary.estimator
        if estimator is None:
            raise ContextError("context_unconfigured")
        value = ModelRequest.model_validate(request.model_dump(mode="json"))
        before = canonical(value.model_dump(mode="json"))
        charge(value.model_dump(mode="json"), self.config.max_source_bytes)
        try:
            answer = await self.executor.workers.call(
                self.gov.log.session.id, estimator.estimate, value
            )
        except ContextError:
            raise
        except Exception:
            raise ContextError("context_failed") from None
        if canonical(value.model_dump(mode="json")) != before:
            raise ContextError("context_invalid")
        estimate = copied(TokenEstimate, answer)
        charge(estimate.model_dump(mode="json"), self.config.max_source_bytes)
        return estimate

    def _notice(self) -> str:
        original = self.original
        if original is None:
            raise ContextError("context_invalid")
        return (
            f"[Current status: pending interactions={original.pending}; "
            f"unresolved effects={original.effects}. "
            "This notice does not authorize execution.]"
            if original.pending or original.effects
            else ""
        )

    async def _input(
        self,
        request: ModelRequest,
        projection: SourceProjection,
        groups: tuple[tuple[int, ...], ...],
        through: int,
    ) -> ContextInput:
        original = self.original
        if original is None:
            raise ContextError("context_invalid")
        fixed = await self.estimate(request.model_copy(update={"messages": ()}))
        messages = {s.index: s.message for s in projection.sources}
        estimates: list[ContextGroup] = []
        for group in groups:
            measured = await self.estimate(
                request.model_copy(update={"messages": tuple(messages[i] for i in group)})
            )
            estimate = measured.model_copy(
                update={"tokens": max(0, measured.tokens - fixed.tokens)}
            )
            estimates.append(ContextGroup(indices=group, estimate=estimate))
        return copied(
            ContextInput,
            ContextInput.model_construct(
                session_id=self.gov.log.session.id,
                through_index=through,
                sources=projection.sources,
                filtered_indices=projection.filtered_indices,
                summaries=(),
                protected=original.protected,
                limits=self.profile,
                source_view_digest=original.view_digest(projection),
                fixed_estimate=fixed,
                groups=tuple(estimates),
            ),
        )

    async def prepare(self, request: ModelRequest) -> ModelRequest:
        scope = self.gov.scope
        with self.gov.observers.tracer.start_as_current_span(
            "tether.context",
            attributes={
                "tether.owner": scope.owner,
                "tether.principal": scope.principal,
                "tether.session_id": scope.session_id,
                "tether.phase_turn_id": scope.phase_turn_id,
                "tether.model_call_id": request.tags.model_call_id,
            },
        ) as span:
            try:
                return await self._prepare(request)
            except ContextError as error:
                span.error(error.code if error.code in CODES else "context_failed")
                raise

    async def _prepare(self, request: ModelRequest) -> ModelRequest:
        original = self.original
        if original is None:
            raise ContextError("context_invalid")
        request = request.model_copy(update={"max_output_tokens": self.profile.output_tokens})
        request, projection = await narrow_sources(
            self.gov,
            self.executor.workers,
            self.executor.registry.providers,
            self.executor.spec,
            request,
            original.sources,
        )
        groups = original.narrowed(projection)
        notice = self._notice()
        if notice:
            request = request.model_copy(update={"system": request.system + "\n\n" + notice})
        await self.gov.authorize("read_session", self.gov.log.session.id)
        ordinal = next_ordinal(tuple(self.gov.log.entries), request.tags.model_call_id)
        through = self.gov.log.entries[-1].index
        summary_profile = self.summary_profile()
        preparation = ContextPreparation(
            model_call_id=request.tags.model_call_id,
            ordinal=ordinal,
            through_index=through,
            source_view_digest=original.view_digest(projection),
            scope_digest=self.scope_digest(),
            spec_digest=digest(self.executor.spec.model_dump(mode="json")),
            config_digest=digest(self.config.model_dump(mode="json")),
            main_profile_digest=digest(self.profile.model_dump(mode="json")),
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            summary_profile_digest=digest(summary_profile.model_dump(mode="json"))
            if summary_profile is not None
            else None,
        )
        charge(preparation.model_dump(mode="json"), self.config.max_source_bytes)
        self.allocation = await self.gov.fact(
            "context_preparation",
            key=f"context:prepare:{ordinal}",
            **preparation.model_dump(mode="json"),
        )
        self.ordinal = ordinal
        self.preparation = preparation
        context = await self._input(request, projection, groups, through)
        context = copied(
            ContextInput, context.model_copy(update={"summaries": compatible(self, context)})
        )
        plan = await self._plan(context)
        context, plan, self.summaries, self.summary_texts = await resolve(
            self, context, plan, request
        )
        await self.verify_sources()
        self.binding_check()
        request = request.model_copy(
            update={"messages": materialize(context, plan, summaries=self.summary_texts)}
        )
        estimate = await self.estimate(request)
        self._fit(estimate)
        use = ContextUse(
            model_call_id=request.tags.model_call_id,
            preparation_ordinal=ordinal,
            config_digest=preparation.config_digest,
            through_index=through,
            parts=merged_parts(context, plan),
            source_view_digest=preparation.source_view_digest,
            request_digest=request_digest(request),
            profile_id=self.profile.profile_id,
            profile_revision=self.profile.revision,
            estimate=estimate,
            output_tokens=self.profile.output_tokens,
            safety_margin_tokens=self.profile.safety_margin_tokens,
        )
        charge(use.model_dump(mode="json"), self.config.max_source_bytes)
        self.accepted = await self.gov.fact(
            "context_use", key=f"context:use:{ordinal}", **use.model_dump(mode="json")
        )
        self.preparation, self.use = preparation, use
        self.request = ModelRequest.model_validate(request.model_dump(mode="json"))
        self.used = True
        return request

    async def _plan(self, context: ContextInput) -> ContextPlan:
        charge(context.model_dump(mode="json"), self.config.max_source_bytes)
        argument = context.model_copy(deep=True)
        before = canonical(argument.model_dump(mode="json"))
        try:
            answer = await self.executor.workers.call(
                self.gov.log.session.id, self.boundary.strategy.plan, argument
            )
        except ContextError:
            raise
        except Exception:
            raise ContextError("context_failed") from None
        if canonical(argument.model_dump(mode="json")) != before:
            raise ContextError("context_invalid")
        plan = copied(ContextPlan, answer)
        charge(plan.model_dump(mode="json"), self.config.max_source_bytes)
        plan = validated_plan(
            context, plan, summary_allowance=self.config.max_summary_calls_per_step
        )
        if (plan.strategy_id, plan.strategy_version) != (self.strategy_id, self.strategy_version):
            raise ContextError("context_invalid")
        return plan

    def _fit(self, estimate: TokenEstimate) -> None:
        if estimate.tokens > self.profile.input_tokens:
            raise ContextError("context_exhausted")

    def summary_profile(self) -> ModelLimits | None:
        tier = self.config.summary_model
        return self.config.limits.get(tier) if tier is not None else None

    def binding_check(self) -> None:
        self._deadline()
        current = self.boundary.configuration(self.tier)
        if (
            digest(current.model_dump(mode="json")) != self.preparation.config_digest
            or self.scope_digest() != self.preparation.scope_digest
            or digest(self.executor.spec.model_dump(mode="json")) != self.preparation.spec_digest
            or (self.boundary.strategy.id, self.boundary.strategy.version)
            != (self.strategy_id, self.strategy_version)
        ):
            raise ContextError("context_incompatible")
        if (
            self.allocation not in self.gov.log.entries
            or ContextPreparation.model_validate(self.allocation.facts) != self.preparation
        ):
            raise ContextError("context_incompatible")
        self._deadline()

    async def verify_sources(self) -> None:
        original = self.original
        if original is None:
            raise ContextError("context_invalid")
        fresh = await capture(self.gov, self.config.max_source_bytes, self.turns)
        if (fresh.sources, fresh.protected, fresh.pending, fresh.effects) != (
            original.sources,
            original.protected,
            original.pending,
            original.effects,
        ):
            raise ContextError("context_incompatible")

    def check(self) -> None:
        """Final nonawaiting admission after all policy/recorder/journal awaits."""
        self._deadline()
        if self.request is None or self.accepted is None or self.original is None:
            raise ContextError("context_invalid")
        self.binding_check()
        if (
            self.accepted not in self.gov.log.entries
            or ContextUse.model_validate(self.accepted.facts) != self.use
            or request_digest(self.request) != self.use.request_digest
        ):
            raise ContextError("context_incompatible")
        for summary in self.summaries:
            entry = next((e for e in self.gov.log.entries if e.index == summary.index), None)
            if (
                entry is None
                or entry.kind != "compaction"
                or CompactionRecord.model_validate(entry.facts) != summary.record
            ):
                raise ContextError("context_incompatible")
        self._deadline()

    async def recheck(self) -> None:
        async with self.guarded():
            self.check()
            request, original = self.request, self.original
            if request is None or original is None:
                raise ContextError("context_invalid")
            await self.verify_sources()
            for summary in self.summaries:
                if await load(self, summary) != self.summary_texts[summary.index]:
                    raise ContextError("context_incompatible")
            estimate = await self.estimate(request)
            self._fit(estimate)
            if estimate != self.use.estimate:
                raise ContextError("context_incompatible")
            await self.gov.authorize("read_session", self.gov.log.session.id)
            self.check()
