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
from typing import Any

import pytest
from selected_profiles_process import await_retained

from tether.engine.workers import Workers
from tether.stores.jsonl import JsonlRoot
from tether.types.execution import ExecutionDenied
from tether.types.lifetime import WorkerLifetime
from tether.types.storage import RootBusy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _await_state_or_terminal(entry: asyncio.Event, *operations: asyncio.Future[Any]) -> None:
    waiting = asyncio.create_task(entry.wait())
    try:
        done, _ = await asyncio.wait({waiting, *operations}, return_when=asyncio.FIRST_COMPLETED)
        entered = waiting in done
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    if entered:
        return
    for operation in operations:
        if operation in done:
            await operation
    raise AssertionError("operation ended before worker entry")


async def _release_abandoned_worker(
    owner_ref: weakref.ReferenceType[Workers],
    waiters: list[weakref.ReferenceType[asyncio.Future[Any]]],
    idle: asyncio.Event,
    release: asyncio.Event,
) -> None:
    release.set()
    pending = waiters[-1]()
    assert pending is not None
    pending.set_result(None)
    owner = owner_ref()
    assert owner is not None
    completion = next(iter(owner.active))
    await completion
    await idle.wait()
    assert not owner.active
    del pending, completion, owner


async def _settle_retained_tasks(*tasks: asyncio.Future[Any]) -> None:
    async def settle() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


async def _cleanup_abandoned_worker(
    owner_ref: weakref.ReferenceType[Workers],
    caller_ref: weakref.ReferenceType[asyncio.Task[Any]],
    waiters: list[weakref.ReferenceType[asyncio.Future[Any]]],
    release: asyncio.Event,
) -> None:
    release.set()
    for waiter_ref in waiters:
        waiter = waiter_ref()
        if waiter is not None and not waiter.done():
            waiter.set_result(None)
    owner, caller = owner_ref(), caller_ref()
    owned: list[asyncio.Future[Any]] = []
    if caller is not None:
        owned.append(caller)
    if owner is not None:
        owned.extend(owner.active)
    if owned:
        await _settle_retained_tasks(*owned)


async def _cancel_worker_until_reentry(
    caller: asyncio.Task[Any],
    workers: Workers,
    entered: asyncio.Event,
    waiting_again: asyncio.Event,
) -> asyncio.Future[Any]:
    await _await_state_or_terminal(entered, caller)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    completion = next(iter(workers.active))
    await _await_state_or_terminal(waiting_again, completion)
    assert not await workers.close(0.001)
    return completion


async def test_state_barrier_surfaces_failed_handoff_while_child_stays_owned():
    entry, child_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held_child():
        child_started.set()
        await release.wait()

    child = asyncio.create_task(held_child())
    await _await_state_or_terminal(child_started, child)
    handoff = asyncio.get_running_loop().create_future()
    handoff.set_exception(RuntimeError("fabricated handoff failure"))
    try:
        with pytest.raises(RuntimeError, match="fabricated handoff failure"):
            await _await_state_or_terminal(entry, child, handoff)
        assert not child.done()
    finally:
        release.set()
        await child


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
        await _await_state_or_terminal(entered, caller)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
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
    entered, returned = asyncio.Event(), Event()
    child_entered, release, adopted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()

    async def child():
        child_entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                adopted.set()
        return "late"

    task = asyncio.create_task(child())
    await _await_state_or_terminal(child_entered, task)

    def function():
        loop.call_soon_threadsafe(entered.set)
        returned.wait()
        return task

    caller = asyncio.create_task(workers.call("session", function))
    try:
        await _await_state_or_terminal(entered, caller)
        completion = next(iter(workers.active))
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        returned.set()
        await _await_state_or_terminal(adopted, task, completion)
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
    entered, returned = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    bodies = []

    async def child():
        bodies.append(True)
        return "should not run"

    coroutine = child()

    def function():
        loop.call_soon_threadsafe(entered.set)
        returned.wait()
        return coroutine

    caller = asyncio.create_task(workers.call("session", function))
    try:
        await _await_state_or_terminal(entered, caller)
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
        await _await_state_or_terminal(entered, caller)
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
    entered, cancelled, waiting_again, idle = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    workers.local_scope.on_idle = idle.set
    waiters = []
    release = asyncio.Event()

    async def child():
        entered.set()
        while not release.is_set():
            private = asyncio.get_running_loop().create_future()
            waiters.append(weakref.ref(private))
            if cancelled.is_set():
                waiting_again.set()
            try:
                await private
            except asyncio.CancelledError:
                cancelled.set()

    caller = asyncio.create_task(workers.call("session", child))
    owner = weakref.ref(workers)
    caller_owner = weakref.ref(caller)
    try:
        completion = await _cancel_worker_until_reentry(caller, workers, entered, waiting_again)
        del caller, workers, completion
        gc.collect()
        assert owner() is not None
        await _release_abandoned_worker(owner, waiters, idle, release)
        gc.collect()
        assert owner() is None
    finally:
        await _cleanup_abandoned_worker(owner, caller_owner, waiters, release)


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
        release.wait()

    first = asyncio.create_task(workers.call("first", blocked))
    try:
        await _await_state_or_terminal(entered, first)
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
        await _await_state_or_terminal(entered, caller)
        assert ambient.get() == "parent"
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await _await_state_or_terminal(settled, next(iter(workers.active)))
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
    await _await_state_or_terminal(entered, task)
    loop = asyncio.get_running_loop()
    value = task
    for _ in range(MAX_AWAITABLE_CHAIN + 2):
        previous = loop.create_future()
        previous.set_result(value)
        value = previous
    operation_started = asyncio.Event()
    original_operation = workers._operation

    def track_operation(session_id, function):
        operation = original_operation(session_id, function)
        operation_started.set()
        return operation

    workers._operation = track_operation
    caller = asyncio.create_task(workers.call("session", lambda: value))
    try:
        await _await_state_or_terminal(operation_started, caller)
        completion = next(iter(workers.active))
        await _await_state_or_terminal(cancelled, task, completion, caller)
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
        await workers.call("session", lambda: first)
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
    await _await_state_or_terminal(entered, child)
    caller = asyncio.create_task(workers.call("session", lambda: child))
    # Raw return/adoption is observed without retaining a waiter on the completion proxy.
    raw_done = asyncio.Event()
    original = workers._executor_finished

    def finished(task):
        original(task)
        raw_done.set()

    workers._executor_finished = finished
    try:
        await _await_state_or_terminal(raw_done, caller)
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
