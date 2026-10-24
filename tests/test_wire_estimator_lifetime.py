"""Counting owns actual CPU completion through deadlines, cancellation and suite cleanup."""

import asyncio
import threading
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar

import pytest
from test_agent_context import fixture, run
from test_wire_estimator import estimator, profile

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
    marker = ContextVar("fabricated-context", default="clean")
    marker.set("caller")

    def blocked(body):
        assert marker.get() == "clean"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)
    config = fabricated_context()["context_config"].model_copy(update={"timeout_seconds": 0.1})
    async with fixture(limits, estimator=counted, context_config=config) as h:
        task = asyncio.create_task(run(h))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            else:
                result = await asyncio.wait_for(task, 1)
                assert (result.stop_reason, result.reason) == (
                    "context_unavailable",
                    "context_failed",
                )
            assert h.engine.workers.busy(h.log.session.id)
            with pytest.raises(WorkerOutstanding, match="worker_outstanding"):
                await run(h)
            assert not h.model.requests
        finally:
            release.set()
            await asyncio.wait_for(
                asyncio.gather(*tuple(h.engine.workers.active), return_exceptions=True), 1
            )
            assert not h.engine.workers.busy(h.log.session.id)


async def test_cancellation_retains_capacity_and_primary_error(model_request, monkeypatch):
    counted = estimator(profile(max_count_operations=1))
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def blocked(body):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        raise ValueError("fabricated secondary failure")

    monkeypatch.setattr(counted, "_count", blocked)
    task = asyncio.create_task(counted.estimate(model_request))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(ContextError, match="context_exhausted"):
            await counted.estimate(model_request)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert counted._active == 0


async def test_queued_cancellation_never_submits_a_second_count(model_request, monkeypatch):
    counted = estimator()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    calls = []
    original = counted._count

    def blocked(body):
        calls.append(body)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)
    first = asyncio.create_task(counted.estimate(model_request))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(counted.estimate(model_request))
        await asyncio.sleep(0)
        assert counted._active == 2
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert len(calls) == 1
    finally:
        release.set()
        await first
    assert counted._active == 0


async def test_suite_deadline_retains_factory_until_actual_count_exit(monkeypatch):
    counted = estimator()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()
    original = counted._count

    def blocked(body):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return original(body)

    monkeypatch.setattr(counted, "_count", blocked)

    @asynccontextmanager
    async def factory():
        try:
            yield TokenEstimatorFixture(counted, lambda request: (0, 100000))
        finally:
            closed.set()

    task = asyncio.create_task(token_estimator_suite(factory, timeout_seconds=0.1))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, 1)
        assert not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(closed.wait(), 1)


async def test_foreign_loop_refuses_before_admission_mutates(model_request):
    counted = estimator()
    await counted.estimate(model_request)

    def foreign():
        with pytest.raises(ContextError, match="context_incompatible"):
            asyncio.run(counted.estimate(model_request))

    await asyncio.to_thread(foreign)
    assert counted._active == 0


async def test_strategy_suite_deadline_keeps_noncooperative_factory_owned():
    from tether.context import FullContext
    from tether.testing.conformance import context_strategy_suite

    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

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
            yield Strategy()
        finally:
            closed.set()

    task = asyncio.create_task(context_strategy_suite(factory, timeout_seconds=0.05))
    try:
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, 1)
        assert not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(closed.wait(), 1)
