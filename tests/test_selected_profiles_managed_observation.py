"""Fabricated controls for managed-process observation watchdog and cleanup ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar, cast

import pytest
import selected_profiles_managed_observation as managed_observation
import selected_profiles_managed_process as managed
import selected_profiles_managed_process_peer as managed_peer
import selected_profiles_process as process_helpers

T = TypeVar("T")


async def _settle_owned_task(task: asyncio.Future[Any]) -> None:
    if not task.done():
        task.cancel()
    caller_cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            caller_cancelled = caller_cancelled or not task.done()
        except Exception:
            break
    if task.done() and not task.cancelled():
        task.exception()
    if caller_cancelled:
        raise asyncio.CancelledError


async def _wait_event_or_owner(event: asyncio.Event, owner: asyncio.Future[Any]) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({waiter, owner}, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            await owner
            raise AssertionError("owner_completed_before_required_state")
        await waiter
    finally:
        await _settle_owned_task(waiter)


async def _yield_to_scheduled_callbacks() -> None:
    loop = asyncio.get_running_loop()
    yielded = asyncio.Event()
    loop.call_soon(yielded.set)
    await yielded.wait()


class _ManualWatchdog:
    def __init__(self) -> None:
        self.owner: asyncio.Task[Any] | None = None
        self._expiration_cancel_count: int | None = None

    async def __aenter__(self) -> None:
        self.owner = asyncio.current_task()

    async def __aexit__(self, error_type: object, _error: object, _traceback: object) -> bool:
        if (
            error_type is asyncio.CancelledError
            and self._expiration_cancel_count is not None
            and self.owner is not None
            and self.owner.cancelling() == self._expiration_cancel_count
        ):
            self.owner.uncancel()
            raise TimeoutError
        return False

    def expired(self) -> bool:
        return self._expiration_cancel_count is not None

    def expire(self) -> None:
        assert self.owner is not None
        self._expiration_cancel_count = self.owner.cancelling() + 1
        self.owner.cancel()


def _install_manual_watchdog(monkeypatch: pytest.MonkeyPatch) -> list[_ManualWatchdog]:
    controls: list[_ManualWatchdog] = []
    original = managed_observation.run_with_observation_watchdog

    def factory(_seconds: float) -> _ManualWatchdog:
        control = _ManualWatchdog()
        controls.append(control)
        return control

    async def run(seconds: float | None, operation: Callable[[], Awaitable[T]]) -> T:
        return await original(seconds, operation, timeout_factory=factory)

    monkeypatch.setattr(managed, "run_with_observation_watchdog", run)
    return controls


def test_managed_qualification_preserves_storage_io_shutdown_and_lease_limits() -> None:
    for limits in (managed._storage_limits(), managed_peer._storage_limits(30)):
        assert limits.lease_seconds == 30
        assert limits.io_seconds == 20
        assert limits.shutdown_seconds == 5


def test_managed_qualification_watchdog_reports_incomplete_only_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watchdogs = _install_manual_watchdog(monkeypatch)

    async def run() -> None:
        observation_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()

        @asynccontextmanager
        async def context(_workspace: object, _values: object):
            try:
                yield cast(managed._Context, object())
            finally:
                task = asyncio.create_task(cleanup())
                await process_helpers.await_retained(task)

        async def database(_context: object) -> dict[str, object]:
            observation_started.set()
            await asyncio.Event().wait()
            return {}

        monkeypatch.setattr(managed, "_managed_context", context)
        monkeypatch.setattr(managed, "_database_observation", database)
        owner = asyncio.create_task(
            managed.qualify_managed_processes(
                cast(managed.WorkspaceClient, object()),
                {},
                observation_timeout_seconds=10,
            )
        )
        try:
            await _wait_event_or_owner(observation_started, owner)
            watchdogs[0].expire()
            await _wait_event_or_owner(cleanup_started, owner)
            assert not owner.done()
            release_cleanup.set()
            with pytest.raises(managed.ManagedPeerInconclusive) as error:
                await owner
            assert str(error.value) == "managed_peer_observation_watchdog"
        finally:
            release_cleanup.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_observation_watchdog_does_not_swallow_repeated_caller_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watchdogs = _install_manual_watchdog(monkeypatch)

    async def run() -> None:
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()

        @asynccontextmanager
        async def context(_workspace: object, _values: object):
            try:
                yield cast(managed._Context, object())
            finally:
                task = asyncio.create_task(cleanup())
                await process_helpers.await_retained(task)

        async def database(_context: object) -> dict[str, object]:
            watchdogs[0].expire()
            await asyncio.Event().wait()
            return {}

        monkeypatch.setattr(managed, "_managed_context", context)
        monkeypatch.setattr(managed, "_database_observation", database)
        owner = asyncio.create_task(
            managed.qualify_managed_processes(
                cast(managed.WorkspaceClient, object()), {}, observation_timeout_seconds=10
            )
        )
        try:
            await _wait_event_or_owner(cleanup_started, owner)
            assert not owner.done()
            owner.cancel()
            owner.cancel()
            await _yield_to_scheduled_callbacks()
            assert not owner.done()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        finally:
            release_cleanup.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_qualification_does_not_relabel_body_timeout_as_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        @asynccontextmanager
        async def context(_workspace: object, _values: object):
            yield cast(managed._Context, object())

        async def database(_context: object) -> dict[str, object]:
            raise TimeoutError("operation timeout")

        monkeypatch.setattr(managed, "_managed_context", context)
        monkeypatch.setattr(managed, "_database_observation", database)
        with pytest.raises(TimeoutError, match="operation timeout"):
            await managed.qualify_managed_processes(
                cast(managed.WorkspaceClient, object()),
                {},
                observation_timeout_seconds=10,
            )

    asyncio.run(run())


def test_managed_observation_resource_acquisition_closes_late_result_after_cancel() -> None:
    async def run() -> None:
        open_started = asyncio.Event()
        release_open = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        resource = object()

        async def open_resource() -> object:
            open_started.set()
            await release_open.wait()
            return resource

        async def close_resource(value: object) -> None:
            assert value is resource
            close_started.set()
            await release_close.wait()

        owner = asyncio.create_task(
            managed_observation.acquire_or_close_on_cancellation(open_resource(), close_resource)
        )
        try:
            await _wait_event_or_owner(open_started, owner)
            owner.cancel()
            await _yield_to_scheduled_callbacks()
            assert not owner.done()
            assert not close_started.is_set()
            release_open.set()
            await _wait_event_or_owner(close_started, owner)
            assert not owner.done()
            release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        finally:
            release_open.set()
            release_close.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_observation_resource_cleanup_survives_repeated_cancellation() -> None:
    async def run() -> None:
        open_started = asyncio.Event()
        release_open = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()

        async def open_resource() -> object:
            open_started.set()
            await release_open.wait()
            return object()

        async def close_resource(_value: object) -> None:
            close_started.set()
            await release_close.wait()

        owner = asyncio.create_task(
            managed_observation.acquire_or_close_on_cancellation(open_resource(), close_resource)
        )
        try:
            await _wait_event_or_owner(open_started, owner)
            owner.cancel()
            await _yield_to_scheduled_callbacks()
            release_open.set()
            await _wait_event_or_owner(close_started, owner)
            for _ in range(2):
                owner.cancel()
                await _yield_to_scheduled_callbacks()
                assert not owner.done()
            release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        finally:
            release_open.set()
            release_close.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_open_failure_after_caller_cancellation_preserves_cancellation() -> None:
    async def run() -> None:
        open_started = asyncio.Event()
        release_open = asyncio.Event()

        async def open_resource() -> object:
            open_started.set()
            await release_open.wait()
            raise RuntimeError("fabricated open failure")

        async def close_resource(_value: object) -> None:
            raise AssertionError("failed acquisition returned a resource")

        owner = asyncio.create_task(
            managed_observation.acquire_or_close_on_cancellation(open_resource(), close_resource)
        )
        try:
            await _wait_event_or_owner(open_started, owner)
            owner.cancel()
            await _yield_to_scheduled_callbacks()
            assert not owner.done()
            release_open.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        finally:
            release_open.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_qualification_defaults_to_state_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        observation_started = asyncio.Event()
        release_observation = asyncio.Event()

        @asynccontextmanager
        async def context(_workspace: object, _values: object):
            yield cast(managed._Context, object())

        async def database(_context: object) -> dict[str, object]:
            observation_started.set()
            await release_observation.wait()
            return {"observed": True}

        async def fencing(_context: object) -> dict[str, object]:
            return {"fencing": True}

        async def recovery(_context: object) -> dict[str, object]:
            return {"recovery": True}

        monkeypatch.setattr(managed, "_managed_context", context)
        monkeypatch.setattr(managed, "_database_observation", database)
        monkeypatch.setattr(managed, "natural_expiry_fencing", fencing)
        monkeypatch.setattr(managed, "killed_worker_recovery", recovery)
        owner = asyncio.create_task(
            managed.qualify_managed_processes(cast(managed.WorkspaceClient, object()), {})
        )
        try:
            await _wait_event_or_owner(observation_started, owner)
            assert not owner.done()
            release_observation.set()
            assert await owner == {
                "database": {"observed": True},
                "natural_expiry_fencing": {"fencing": True},
                "killed_worker_recovery": {"recovery": True},
            }
        finally:
            release_observation.set()
            if not owner.done():
                owner.cancel()
            await _settle_owned_task(owner)

    asyncio.run(run())


def test_managed_observation_watchdog_reports_only_its_own_expiry() -> None:
    async def run() -> None:
        with pytest.raises(managed_observation.ObservationWatchdogExpired):
            await managed_observation.run_with_observation_watchdog(0.01, asyncio.Event().wait)

        async def body_timeout() -> object:
            raise TimeoutError("operation timeout")

        with pytest.raises(TimeoutError, match="operation timeout"):
            await managed_observation.run_with_observation_watchdog(10, body_timeout)

    asyncio.run(run())


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), float("nan")])
def test_managed_observation_watchdog_rejects_invalid_values(seconds: object) -> None:
    async def operation() -> None:
        async def completed() -> None:
            return None

        await managed_observation.run_with_observation_watchdog(
            cast(float | None, seconds), completed
        )

    with pytest.raises(ValueError, match="managed_observation_watchdog_invalid"):
        asyncio.run(operation())
