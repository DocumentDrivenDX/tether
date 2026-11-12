"""Counting owns actual CPU completion through deadlines, cancellation and suite cleanup."""

import asyncio
import threading
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar

import pytest
from context_lifetime_controls import (
    CancellationObserver,
    ContextClock,
    TimeoutCapture,
    settle_tasks,
    wait_event_or_task,
)
from test_agent_context import fixture, run
from test_wire_estimator import estimator, profile

import tether.adapters.pydantic_ai.estimator as estimator_module
import tether.engine.context.step as context_step
import tether.testing.conformance.context as conformance_context
from tether.context.testing import fabricated_context
from tether.testing.conformance import TokenEstimatorFixture, token_estimator_suite
from tether.types.context import ContextError
from tether.types.execution import WorkerOutstanding

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("cancel", [False, True])
async def test_engine_deadline_keeps_actual_count_owned_until_thread_exit(
    limits, monkeypatch, cancel
):
    counted = estimator()
    original = counted._count
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    clock = ContextClock()
    clock.install(context_step, monkeypatch)
    marker = ContextVar("fabricated-context", default="clean")
    marker.set("caller")

    def blocked(body):
        assert marker.get() == "clean"
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)
    config = fabricated_context()["context_config"].model_copy(update={"timeout_seconds": 0.1})
    async with fixture(limits, estimator=counted, context_config=config) as h:
        task = asyncio.create_task(run(h))
        owned_workers = ()
        try:
            await wait_event_or_task(entered, task)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                clock.advance_past(clock.deadlines[-1])
                timer = clock.timers[-1]
                timer.reschedule(loop.time())
                result = await task
                assert timer.expired()
                assert (result.stop_reason, result.reason) == (
                    "context_unavailable",
                    "context_failed",
                )
            assert h.engine.workers.busy(h.log.session.id)
            with pytest.raises(WorkerOutstanding, match="worker_outstanding"):
                await run(h)
            assert not h.model.requests
            owned_workers = tuple(h.engine.workers.active)
        finally:
            release.set()
            await settle_tasks((task, *owned_workers, *tuple(h.engine.workers.active)))
            assert not h.engine.workers.busy(h.log.session.id)


async def test_cancellation_retains_capacity_and_primary_error(model_request, monkeypatch):
    counted = estimator(profile(max_count_operations=1))
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    cancellation = CancellationObserver()
    cancellation.install(estimator_module, monkeypatch)

    def blocked(body):
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        raise ValueError("fabricated secondary failure")

    monkeypatch.setattr(counted, "_count", blocked)
    task = asyncio.create_task(counted.estimate(model_request))
    try:
        await wait_event_or_task(entered, task)
        await wait_event_or_task(cancellation.entered, task)
        task.cancel()
        await wait_event_or_task(cancellation.acknowledged[0], task)
        assert not task.done()
        task.cancel()
        await wait_event_or_task(cancellation.acknowledged[1], task)
        assert not task.done()
        with pytest.raises(ContextError, match="context_exhausted"):
            await counted.estimate(model_request)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await settle_tasks((task,))
    assert counted._active == 0


async def test_queued_cancellation_never_submits_a_second_count(model_request, monkeypatch):
    counted = estimator()
    entered, release = asyncio.Event(), threading.Event()
    second_admitted = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []
    original = counted._count

    def blocked(body):
        calls.append(body)
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)
    first = asyncio.create_task(counted.estimate(model_request))
    second = None
    original_admit = counted._admit
    admissions = 0

    def observed_admit():
        nonlocal admissions
        semaphore = original_admit()
        admissions += 1
        if admissions == 1:
            second_admitted.set()
        return semaphore

    try:
        await wait_event_or_task(entered, first)
        monkeypatch.setattr(counted, "_admit", observed_admit)
        second = asyncio.create_task(counted.estimate(model_request))
        await wait_event_or_task(second_admitted, second)
        assert counted._active == 2
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert len(calls) == 1
        release.set()
        await first
    finally:
        release.set()
        tasks = (first,) if second is None else (first, second)
        await settle_tasks(tasks)
    assert counted._active == 0


async def test_suite_deadline_retains_factory_until_actual_count_exit(monkeypatch):
    counted = estimator()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()
    original = counted._count
    timeouts = TimeoutCapture()
    timeouts.install(conformance_context, monkeypatch)

    def blocked(body):
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)

    @asynccontextmanager
    async def factory():
        try:
            factory_entered.set()
            yield TokenEstimatorFixture(counted, lambda request: (0, 100000))
        finally:
            closed.set()

    factory_entered = asyncio.Event()
    task = asyncio.create_task(token_estimator_suite(factory, timeout_seconds=0.1))
    try:
        await wait_event_or_task(entered, task)
        timer = timeouts.arm_last()
        with pytest.raises(TimeoutError):
            await task
        assert timer.expired()
        assert not closed.is_set()
    finally:
        cleanup = asyncio.create_task(
            settle_tasks((task,), release=release.set, close=closed, entered=factory_entered)
        )
        await settle_tasks((cleanup,))
    assert counted._active == 0


async def test_foreign_loop_refuses_before_admission_mutates(model_request):
    counted = estimator()
    await counted.estimate(model_request)

    def foreign():
        with pytest.raises(ContextError, match="context_incompatible"):
            asyncio.run(counted.estimate(model_request))

    await asyncio.to_thread(foreign)
    assert counted._active == 0


async def test_strategy_suite_deadline_keeps_noncooperative_factory_owned(monkeypatch):
    from tether.context import FullContext
    from tether.testing.conformance import context_strategy_suite

    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    factory_entered = asyncio.Event()
    timeouts = TimeoutCapture()
    timeouts.install(conformance_context, monkeypatch)

    class Strategy(FullContext):
        async def plan(self, context):
            entered.set()
            while not release.is_set():
                with suppress(asyncio.CancelledError):
                    await release.wait()
            return await super().plan(context)

    @asynccontextmanager
    async def factory():
        try:
            factory_entered.set()
            yield Strategy()
        finally:
            closed.set()

    task = asyncio.create_task(context_strategy_suite(factory, timeout_seconds=0.05))
    try:
        await wait_event_or_task(entered, task)
        timer = timeouts.arm_last()
        with pytest.raises(TimeoutError):
            await task
        assert timer.expired()
        assert not closed.is_set()
    finally:
        cleanup = asyncio.create_task(
            settle_tasks((task,), release=release.set, close=closed, entered=factory_entered)
        )
        await settle_tasks((cleanup,))


async def test_phase_wait_drains_observer_after_operation_terminal_before_entry():
    entered, observer_started, observer_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    failure = RuntimeError("fabricated primary failure")

    async def fail_before_entry():
        raise failure

    task = asyncio.create_task(fail_before_entry())
    with pytest.raises(RuntimeError) as caught:
        await wait_event_or_task(
            entered,
            task,
            waiter_started=observer_started,
            waiter_finished=observer_finished,
        )
    assert caught.value is failure
    assert observer_started.is_set()
    assert observer_finished.is_set()


async def test_repeated_cancellation_retains_every_owned_settlement_task():
    release, first_entered, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first_exited, second_exited = asyncio.Event(), asyncio.Event()

    async def held(entered, exited):
        entered.set()
        try:
            await release.wait()
        finally:
            exited.set()

    first = asyncio.create_task(held(first_entered, first_exited))
    second = asyncio.create_task(held(second_entered, second_exited))
    settlement_started = asyncio.Event()
    cancellation_acks = (asyncio.Event(), asyncio.Event())
    settlement = None
    try:
        await wait_event_or_task(first_entered, first)
        await wait_event_or_task(second_entered, second)
        settlement = asyncio.create_task(
            settle_tasks(
                (first, second),
                started=settlement_started,
                cancellation_acks=cancellation_acks,
            )
        )
        await wait_event_or_task(settlement_started, settlement)
        settlement.cancel()
        await wait_event_or_task(cancellation_acks[0], settlement)
        assert not settlement.done()
        assert not first_exited.is_set()
        assert not second_exited.is_set()
        settlement.cancel()
        await wait_event_or_task(cancellation_acks[1], settlement)
        assert not settlement.done()
        assert not first_exited.is_set()
        assert not second_exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await settlement
        assert first_exited.is_set()
        assert second_exited.is_set()
    finally:
        release.set()
        tasks = (first, second) if settlement is None else (first, second, settlement)
        await settle_tasks(tasks)
