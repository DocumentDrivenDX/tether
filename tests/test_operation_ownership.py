"""Operation proxies retain actual async/returned work without consuming thread permits."""

from __future__ import annotations

import asyncio
import gc
import inspect
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import ContextVar
from threading import Event

import pytest

from tether.engine.workers import Workers
from tether.stores.jsonl import JsonlRoot
from tether.types.execution import ExecutionDenied
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import RootBusy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_async_cancellation_returns_without_releasing_actual_child_or_root(tmp_path):
    root = await JsonlRoot.open(tmp_path)
    root.limits = root.limits.model_copy(update={"shutdown_seconds": 0.01})
    workers = Workers(1)
    workers.scopes["session"] = root.workers
    entered, release = asyncio.Event(), asyncio.Event()

    async def child():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        return "late result"

    caller = asyncio.create_task(workers.call("session", child))
    try:
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, 0.5)
        assert root.workers.busy("session")
        assert not await workers.close(0.01)
        assert not await root.aclose()
        with pytest.raises(RootBusy):
            await JsonlRoot.open(tmp_path)
    finally:
        release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        assert not workers.active
        assert await root.aclose()
    replacement = await JsonlRoot.open(tmp_path)
    assert await replacement.aclose()


async def test_executor_return_handoff_never_publishes_idle_and_adopts_after_cancel():
    idle = []
    scope = WorkerLifetime(lambda: idle.append(True))
    workers = Workers(1)
    workers.scopes["session"] = scope
    entered, returned = Event(), Event()
    child_entered, release, adopted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def child():
        child_entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                adopted.set()
        return "late"

    task = asyncio.create_task(child())
    await child_entered.wait()

    def function():
        entered.set()
        if not returned.wait(3):
            raise TimeoutError("fabricated return barrier")
        return task

    caller = asyncio.create_task(workers.call("session", function))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        returned.set()
        await adopted.wait()
        assert not workers.executors
        assert scope.busy("session")
        assert not idle
        assert not await workers.close(0.01)
    finally:
        returned.set()
        release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
    assert idle == [True]
    assert not scope.active


async def test_cancel_before_raw_return_does_not_start_fresh_coroutine():
    workers = Workers(1)
    entered, returned = Event(), Event()
    bodies = []

    async def child():
        bodies.append(True)
        return "should not run"

    coroutine = child()

    def function():
        entered.set()
        if not returned.wait(3):
            raise TimeoutError("fabricated return barrier")
        return coroutine

    caller = asyncio.create_task(workers.call("session", function))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        returned.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
    assert not bodies
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


@pytest.mark.parametrize("returned", [False, True])
async def test_nested_mediated_work_with_single_physical_permit(returned):
    workers = Workers(1)

    async def inner():
        return await workers.call("session", lambda: "nested")

    function = (lambda: inner()) if returned else inner
    assert await workers.call("session", function) == "nested"
    assert not workers.active
    assert not workers.executors


async def test_operation_bound_refuses_before_starting_another_body():
    workers = Workers(1, operation_capacity=1)
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = []

    async def held():
        entered.set()
        await release.wait()

    caller = asyncio.create_task(workers.call("first", held))
    try:
        await entered.wait()
        with pytest.raises(ExecutionDenied, match="operation_capacity"):
            await workers.call("second", lambda: bodies.append(True))
        assert not bodies
    finally:
        release.set()
        await caller


async def test_foreign_loop_future_refuses_before_lifetime_mutation_or_await():
    scope = WorkerLifetime()
    loop = asyncio.new_event_loop()
    foreign = loop.create_future()
    try:
        with pytest.raises(RuntimeError, match="wrong_event_loop"):
            scope.retain("session", foreign)
        assert not scope.active
        workers = Workers(1)
        with pytest.raises(ExecutionDenied, match="worker_awaitable_loop"):
            await workers.call("session", lambda: foreign)
        assert not foreign.done()
        assert not workers.active
    finally:
        foreign.cancel()
        loop.close()


async def test_actual_async_child_retained_after_dropping_all_caller_owners():
    workers = Workers(1)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    waiters = []
    released = False

    async def child():
        entered.set()
        while not released:
            private = asyncio.get_running_loop().create_future()
            waiters.append(weakref.ref(private))
            try:
                await private
            except asyncio.CancelledError:
                cancelled.set()

    caller = asyncio.create_task(workers.call("session", child))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await cancelled.wait()
    assert not await workers.close(0.001)
    owner = weakref.ref(workers)
    del caller, workers
    for _ in range(10):
        await asyncio.sleep(0)
    gc.collect()
    assert owner() is not None
    released = True
    waiters[-1]().set_result(None)
    for _ in range(10):
        await asyncio.sleep(0)
    gc.collect()
    assert owner() is None


async def test_queued_cancellation_never_enters_function(monkeypatch):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    pool = ThreadPoolExecutor(max_workers=1)

    def submit(executor, function, *args):
        return original(pool if executor is None else executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", submit)
    workers = Workers(2)
    entered, release = asyncio.Event(), Event()
    effects = []

    def blocked():
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(3):
            raise TimeoutError("fabricated release barrier")

    first = asyncio.create_task(workers.call("first", blocked))
    try:
        await entered.wait()
        queued = asyncio.create_task(workers.call("second", lambda: effects.append(True)))
        await asyncio.sleep(0)
        assert len(workers.executors) == 2
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert workers.busy("second")
        release.set()
        await first
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
        assert not effects
    finally:
        release.set()
        monkeypatch.setattr(loop, "run_in_executor", original)
        await asyncio.gather(first, return_exceptions=True)
        await asyncio.to_thread(pool.shutdown)


async def test_late_child_failure_is_consumed_and_parent_context_isolated():
    workers = Workers(1)
    settled = asyncio.Event()
    workers.local_scope.on_idle = settled.set
    entered, release = asyncio.Event(), asyncio.Event()
    ambient = ContextVar("fabricated_operation_context", default="none")
    ambient.set("parent")

    async def child():
        assert ambient.get() == "parent"
        ambient.set("child")
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        raise RuntimeError("fabricated late failure marker")

    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    caller = asyncio.create_task(workers.call("session", child))
    try:
        await entered.wait()
        assert ambient.get() == "parent"
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await settled.wait()
        assert not workers.active
        del caller
        gc.collect()
        assert not unhandled
    finally:
        release.set()
        loop.set_exception_handler(previous)


@pytest.mark.parametrize("stage", ["submit", "task"])
async def test_admission_creation_failure_releases_known_not_started_work(monkeypatch, stage):
    workers = Workers(1)
    loop = asyncio.get_running_loop()
    bodies = []

    def fail(*args, **kwargs):
        raise RuntimeError("fabricated creation failure")

    async def child():
        bodies.append(True)

    def plain():
        bodies.append(True)

    previous = loop.get_task_factory()
    if stage == "submit":
        monkeypatch.setattr(loop, "run_in_executor", fail)

    else:
        loop.set_task_factory(fail)
    try:
        with pytest.raises(RuntimeError, match="fabricated creation failure"):
            await workers.call("session", plain if stage == "submit" else child)
    finally:
        if stage == "task":
            loop.set_task_factory(previous)
    await asyncio.sleep(0)
    assert not workers.active
    assert not bodies
    assert not workers.executors


async def test_depth_rejection_drains_later_running_task_without_releasing_ownership():
    from tether.engine.operations import MAX_AWAITABLE_CHAIN

    workers = Workers(1)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return "late"

    task = asyncio.create_task(held())
    await entered.wait()
    loop = asyncio.get_running_loop()
    value = task
    for _ in range(MAX_AWAITABLE_CHAIN + 2):
        previous = loop.create_future()
        previous.set_result(value)
        value = previous
    caller = asyncio.create_task(workers.call("session", lambda: value))
    try:
        await cancelled.wait()
        assert workers.busy("session")
        assert not caller.done()
        assert not await workers.close(0.001)
    finally:
        release.set()
        with pytest.raises(ExecutionDenied, match="worker_awaitable_depth"):
            await caller
    assert not workers.active


async def test_resolved_future_cycle_is_refused_without_spinning_forever():
    workers = Workers(1)
    loop = asyncio.get_running_loop()
    first, second = loop.create_future(), loop.create_future()
    first.set_result(second)
    second.set_result(first)
    with pytest.raises(ExecutionDenied, match="worker_awaitable_cycle"):
        await asyncio.wait_for(workers.call("session", lambda: first), 1)
    assert not workers.active


async def test_cancel_request_failure_preserves_waiter_and_actual_child_ownership():
    workers = Workers(1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held():
        entered.set()
        await release.wait()
        return "late"

    class RefusesCancel(asyncio.Task):
        def cancel(self, *args, **kwargs):
            raise RuntimeError("fabricated cancellation failure")

    child = RefusesCancel(held())
    await entered.wait()
    caller = asyncio.create_task(workers.call("session", lambda: child))
    # Raw return/adoption is observed without retaining a waiter on the completion proxy.
    raw_done = asyncio.Event()
    original = workers._executor_finished

    def finished(task):
        original(task)
        raw_done.set()

    workers._executor_finished = finished
    try:
        await raw_done.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert workers.busy("session")
    finally:
        release.set()
        await asyncio.gather(*tuple(workers.active), return_exceptions=True)
    assert not workers.active


async def test_depth_refusal_closes_fresh_coroutine_before_its_body():
    from tether.engine.operations import MAX_AWAITABLE_CHAIN

    workers = Workers(1)
    bodies, coroutines = [], []

    def nested(depth):
        async def body():
            bodies.append(depth)
            return nested(depth - 1) if depth else "done"

        coroutine = body()
        coroutines.append(coroutine)
        return coroutine

    with pytest.raises(ExecutionDenied, match="worker_awaitable_depth"):
        await workers.call("session", lambda: nested(MAX_AWAITABLE_CHAIN))
    assert len(bodies) == MAX_AWAITABLE_CHAIN
    assert all(
        inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED for coroutine in coroutines
    )
    assert not workers.active
