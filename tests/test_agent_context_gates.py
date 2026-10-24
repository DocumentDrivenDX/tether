"""Failures, revocation and physical attempts cannot use uncommitted or stale context."""

import asyncio

import pytest
from test_agent_context import fixture, run
from test_gated_execution import final

from tether.context import FullContext
from tether.context.testing import FixedEstimator, fabricated_context
from tether.engine.governance import AllowAll
from tether.models.testing import ScriptedModel
from tether.models.testing.scripted import observe_fixture, prepare_fixture
from tether.types import ModelResponse
from tether.types.context import TokenEstimate
from tether.types.execution import CommitUnknown, Decision
from tether.types.storage import StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("kind", ["context_preparation", "context_use"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_rejected_or_unknown_context_facts_never_enter_model_port(limits, kind, unknown):
    async with fixture(limits) as h:
        original = h.log.append

        async def append(entries):
            if any(e.kind == kind for e in entries):
                if unknown:
                    await original(entries)
                raise (
                    CommitUnknown("fabricated_unknown")
                    if unknown
                    else StoreError("fabricated_rejected")
                )
            return await original(entries)

        h.log.append = append
        with pytest.raises(CommitUnknown if unknown else StoreError, match="fabricated_"):
            await run(h)
        assert not h.model.requests
        assert bool([e for e in h.log.entries if e.kind == kind]) == unknown
        assert not [e for e in h.log.entries if e.kind == "model_attempt"]


async def test_initial_history_hydration_is_inside_context_deadline(limits):
    entered, finished = asyncio.Event(), asyncio.Event()
    config = fabricated_context()["context_config"].model_copy(update={"timeout_seconds": 0.02})
    async with fixture(limits, context_config=config) as h:
        original = h.history.read

        async def read(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
            return await original(*args, **kwargs)

        h.history.read = read
        result = await asyncio.wait_for(run(h), 1)
        assert entered.is_set()
        assert finished.is_set()
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_failed")
        assert not h.model.requests


async def test_timed_out_estimator_retains_its_actual_worker_until_exit(limits):
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Blocked:
        async def estimate(self, request):
            entered.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return TokenEstimate(
                    tokens=0, method="fabricated", revision="1", quality="approximate"
                )
            finally:
                exited.set()

    config = fabricated_context()["context_config"].model_copy(update={"timeout_seconds": 0.02})
    async with fixture(limits, context_config=config, estimator=Blocked()) as h:
        result = await asyncio.wait_for(run(h), 1)
        assert entered.is_set()
        assert not exited.is_set()
        assert result.reason == "context_failed"
        assert not h.model.requests
        assert h.engine.workers.busy(h.log.session.id)
        release.set()
        await asyncio.wait_for(exited.wait(), 1)


@pytest.mark.parametrize("kind", ["estimator", "strategy"])
async def test_callback_mutation_refuses_without_egress(limits, kind):
    class MutatingEstimator(FixedEstimator):
        async def estimate(self, request):
            answer = await super().estimate(request)
            object.__setattr__(request, "system", "Fabricated mutation")
            return answer

    class MutatingStrategy(FullContext):
        async def plan(self, context):
            answer = await super().plan(context)
            object.__setattr__(context.sources[0].message, "content", "Fabricated mutation")
            return answer

    options = (
        {"estimator": MutatingEstimator(0)}
        if kind == "estimator"
        else {"context": MutatingStrategy()}
    )
    async with fixture(limits, **options) as h:
        result = await run(h)
        assert result.reason == "context_invalid"
        assert not h.model.requests


async def test_current_read_revocation_before_physical_attempt_prevents_start(limits):
    class Policy(AllowAll):
        denied = False

        async def authorize(self, request):
            return Decision(
                allowed=not (self.denied and request.action == "read_session"),
                reason="denied" if self.denied else "allowed",
            )

    class Revoke(ScriptedModel):
        async def stream(self, request):
            policy.denied = True
            async for item in super().stream(request):
                yield item

    policy = Policy()
    async with fixture(limits, policy=policy, model=Revoke([final()])) as h:
        result = await run(h)
        assert result.stop_reason == "model_denied"
        assert not [e for e in h.log.entries if e.kind == "model_attempt"]
        assert len([e for e in h.log.entries if e.kind == "context_use"]) == 1


@pytest.mark.parametrize("change", ["profile", "source", "estimate", "accepted_use"])
@pytest.mark.parametrize("custom", [False, True])
async def test_physical_retry_rechecks_frozen_use_without_replanning(limits, change, custom):
    class Counting(FullContext):
        calls = 0

        async def plan(self, context):
            self.calls += 1
            return await super().plan(context)

    class Estimator(FixedEstimator):
        changed = False

        async def estimate(self, request):
            answer = await super().estimate(request)
            return answer.model_copy(update={"tokens": 1000000}) if self.changed else answer

    class Retry:
        provider = "fabricated-retry"
        egress = 0

        async def stream(self, request):
            first = await prepare_fixture(request)
            self.egress += 1
            await observe_fixture(
                request, first, ModelResponse(content="", finish_reason="unknown"), self.provider
            )
            if change == "profile":
                h.engine.context.config.limits["demo"] = h.engine.context.config.limits[
                    "demo"
                ].model_copy(update={"revision": "changed"})
            elif change == "source":
                key = next(iter(h.history._values))
                owner, message = h.history._values[key]
                h.history._values[key] = (
                    owner,
                    message.model_copy(update={"content": "Fabricated changed source"}),
                )
            elif change == "accepted_use":
                entry = next(e for e in h.log._entries if e.kind == "context_use")
                entry.facts["request_digest"] = "0" * 64
            else:
                estimator.changed = True
            await prepare_fixture(request)
            self.egress += 1
            yield final()

    strategy, estimator, model = Counting(), Estimator(0), Retry()
    from tether.models.testing import ListAttemptRecorder

    async with fixture(
        limits,
        context=strategy,
        estimator=estimator,
        model=model,
        attempt_factory=ListAttemptRecorder if custom else None,
    ) as h:
        result = await run(h)
        assert result.stop_reason in {"context_unavailable", "model_denied"}
        assert model.egress == 1
        assert strategy.calls == 1
        assert len([e for e in h.log.entries if e.kind == "model_attempt"]) == (0 if custom else 1)
        assert len([e for e in h.log.entries if e.kind == "context_preparation"]) == 1


async def test_cancellation_during_strategy_propagates_without_context_use(limits):
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Blocked(FullContext):
        async def plan(self, context):
            entered.set()
            try:
                await release.wait()
                return await super().plan(context)
            finally:
                exited.set()

    async with fixture(limits, context=Blocked()) as h:
        task = asyncio.create_task(run(h))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not h.model.requests
        assert not [e for e in h.log.entries if e.kind == "context_use"]
        assert [e for e in h.log.entries if e.kind == "turn_ended"][-1].facts[
            "stop_reason"
        ] == "cancelled"
        release.set()
        await asyncio.wait_for(exited.wait(), 1)


@pytest.mark.parametrize("custom", [False, True])
async def test_read_revocation_during_final_estimate_stops_physical_attempt(limits, custom):
    from tether.models.testing import ListAttemptRecorder

    class Policy(AllowAll):
        denied = False

        async def authorize(self, request):
            return Decision(
                allowed=not (self.denied and request.action == "read_session"),
                reason="denied" if self.denied else "allowed",
            )

    class Revoke(FixedEstimator):
        async def estimate(self, request):
            answer = await super().estimate(request)
            if any(e.kind == "context_use" for e in h.log.entries):
                policy.denied = True
            return answer

    class Model:
        provider = "fabricated"
        egress = 0

        async def stream(self, request):
            await prepare_fixture(request)
            self.egress += 1
            yield final()

    policy, model = Policy(), Model()
    async with fixture(
        limits,
        policy=policy,
        estimator=Revoke(0),
        model=model,
        attempt_factory=ListAttemptRecorder if custom else None,
    ) as h:
        result = await run(h)
        assert result.stop_reason == "model_denied"
        assert model.egress == 0
        assert not [e for e in h.log.entries if e.kind == "model_attempt"]


@pytest.mark.parametrize("custom", [False, True])
async def test_final_policy_wait_cannot_admit_physical_start_after_context_deadline(limits, custom):
    from tether.models.testing import ListAttemptRecorder

    class Policy(AllowAll):
        physical = False

        async def authorize(self, request):
            if self.physical and request.action == "call_model":
                entered.set()
                await release.wait()
            return await super().authorize(request)

    class Model:
        provider = "fabricated"
        egress = 0

        async def stream(self, request):
            policy.physical = True
            await prepare_fixture(request)
            self.egress += 1
            yield final()

    entered, release = asyncio.Event(), asyncio.Event()
    policy, model = Policy(), Model()
    config = fabricated_context()["context_config"].model_copy(update={"timeout_seconds": 0.1})
    async with fixture(
        limits,
        model=model,
        policy=policy,
        context_config=config,
        attempt_factory=ListAttemptRecorder if custom else None,
    ) as h:
        task = asyncio.create_task(run(h))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            deadline = h.engine._runs[h.log.session.id].preparation.deadline
            remaining = max(0, deadline - asyncio.get_running_loop().time()) + 0.02
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(remaining):
                    await asyncio.Event().wait()
        finally:
            release.set()
        result = await asyncio.wait_for(task, 1)
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_failed")
        assert model.egress == 0


async def test_malformed_typed_history_output_is_refused_without_source_content_error(limits):
    async with fixture(limits) as h:
        original = h.history.read

        async def read(*args, **kwargs):
            message = await original(*args, **kwargs)
            return message.model_copy(update={"unexpected": "Fabricated private source marker"})

        h.history.read = read
        result = await run(h)
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_invalid")
        assert not h.model.requests
        assert "Fabricated private source marker" not in str(
            [e.model_dump() for e in h.log.entries]
        )


async def test_strategy_identity_failure_uses_a_closed_context_code(limits):
    class Invalid:
        @property
        def id(self):
            raise RuntimeError("Fabricated private strategy marker")

        version = "1"

        async def plan(self, context):
            raise AssertionError("invalid identity must refuse before strategy invocation")

    async with fixture(limits, context=Invalid()) as h:
        result = await run(h)
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_failed")
        assert not h.model.requests
        assert "Fabricated private strategy marker" not in str(
            [e.model_dump() for e in h.log.entries]
        )


@pytest.mark.parametrize("kind", ["estimate", "strategy"])
async def test_callback_metadata_obeys_context_byte_bound(limits, kind):
    class HugeEstimate(FixedEstimator):
        async def estimate(self, request):
            return (await super().estimate(request)).model_copy(update={"method": "v" * 5000})

    class HugeIdentity(FullContext):
        version = "v" * 5000
        called = False

        async def plan(self, context):
            self.called = True
            return await super().plan(context)

    strategy = HugeIdentity() if kind == "strategy" else FullContext()
    config = fabricated_context()["context_config"].model_copy(update={"max_source_bytes": 2000})
    async with fixture(
        limits,
        context_config=config,
        context=strategy,
        estimator=HugeEstimate(0) if kind == "estimate" else FixedEstimator(0),
    ) as h:
        result = await run(h)
        assert (result.stop_reason, result.reason) == ("context_unavailable", "context_exhausted")
        assert not h.model.requests
        if kind == "strategy":
            assert isinstance(strategy, HugeIdentity)
            assert not strategy.called
