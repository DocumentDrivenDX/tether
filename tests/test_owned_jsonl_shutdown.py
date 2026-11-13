"""Owned facade storage drains actual file I/O after its bounded close expires."""

import asyncio
import os
import sys
from dataclasses import replace
from threading import Event

import pytest
import resource_lifetime_controls
from context_lifetime_controls import settle_tasks
from resource_lifetime_controls import (
    cancel_and_expect_cancellation,
    close_after_release,
    wait_closed_entry,
)
from test_public_reference_profiles import configuration
from tui_event_waits import wait_for_event_or_operation

from tether.client import Actor, ClientHost, LocalJsonlSettings, RootBusy
from tether.stores.jsonl import JsonlRoot
from tether.types.storage import StorageLimits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class HeldCloseOwner:
    def __init__(self, *, close_error: BaseException | None = None):
        self.close_error = close_error
        self.close_entered = asyncio.Event()
        self.wait_entered = asyncio.Event()
        self.cancel_ack = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()

    async def aclose(self) -> bool:
        self.close_entered.set()
        if self.close_error is not None:
            raise self.close_error
        return False

    async def wait_closed(self) -> bool:
        self.wait_entered.set()
        try:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancel_ack.set()
                await self.release.wait()
            return True
        finally:
            self.exited.set()


async def test_owned_jsonl_wait_closed_tracks_actual_file_worker_after_timeout(
    tmp_path, client_limits, limits, monkeypatch
):
    """@covers US-065-AC5"""
    original_open = JsonlRoot.open

    async def bounded_root(_cls, path):
        return await original_open(path, limits=StorageLimits(shutdown_seconds=0.01))

    monkeypatch.setattr(JsonlRoot, "open", classmethod(bounded_root))
    config = replace(
        configuration(client_limits, limits),
        storage=LocalJsonlSettings(tmp_path),
        client_limits=client_limits.model_copy(update={"shutdown_timeout_seconds": 0.01}),
    )
    host = await ClientHost.open(config)
    entered, release = asyncio.Event(), Event()
    operation = None
    waiter = None
    try:
        loop, original_fsync = asyncio.get_running_loop(), os.fsync

        def held_fsync(descriptor):
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            original_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", held_fsync)
        operation = asyncio.create_task(host.client(Actor(id="owner")).create(spec="demo"))
        await wait_for_event_or_operation(entered, operation)
        await cancel_and_expect_cancellation(operation)
        assert await host.aclose() is False

        waiter = await wait_closed_entry(host)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not host.drained
        with pytest.raises(RootBusy, match="local_root_already_open"):
            await ClientHost.open(config)
    finally:
        await close_after_release(host, release.set, operation, waiter, primary=sys.exception())
    assert host.drained
    async with await ClientHost.open(config) as reopened:
        assert len((await reopened.client(Actor(id="owner")).list()).items) == 1


async def test_cleanup_drain_error_keeps_primary_failure_and_waits_for_release():
    close_error = RuntimeError("fabricated_close_failure")
    owner = HeldCloseOwner(close_error=close_error)
    primary = ValueError("fabricated_primary_failure")

    async def preserve_primary():
        try:
            raise primary
        except ValueError as error:
            await close_after_release(owner, lambda: None, primary=error)
            raise

    caller = asyncio.create_task(preserve_primary())
    try:
        await wait_for_event_or_operation(owner.wait_entered, caller)
        assert owner.close_entered.is_set()
        assert not caller.done()
        owner.release.set()
        with pytest.raises(ValueError, match="fabricated_primary_failure") as caught:
            await caller
        assert caught.value is primary
        assert owner.exited.is_set()
    finally:
        owner.release.set()
        await settle_tasks((caller,))


async def test_wait_closed_entry_retains_cancellation_resistant_unpublished_waiter(monkeypatch):
    owner = HeldCloseOwner()
    observed_entry = asyncio.Event()
    cleanup_started, second_cancel_ack = asyncio.Event(), asyncio.Event()

    original_wait_for_entry = resource_lifetime_controls.wait_for_event_or_operation

    async def pause_after_entry(event, operation):
        await original_wait_for_entry(event, operation)
        observed_entry.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        resource_lifetime_controls, "wait_for_event_or_operation", pause_after_entry
    )
    caller = asyncio.create_task(
        wait_closed_entry(
            owner,
            cleanup_started=cleanup_started,
            cancellation_acks=(second_cancel_ack,),
        )
    )
    try:
        await wait_for_event_or_operation(observed_entry, caller)
        caller.cancel()
        await wait_for_event_or_operation(cleanup_started, caller)
        await wait_for_event_or_operation(owner.cancel_ack, caller)
        assert not caller.done()
        caller.cancel()
        await wait_for_event_or_operation(second_cancel_ack, caller)
        assert not caller.done()
        owner.release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert owner.exited.is_set()
    finally:
        owner.release.set()
        await settle_tasks((caller,))
