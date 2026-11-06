"""Owned facade storage drains actual file I/O after its bounded close expires."""

import asyncio
import os
from dataclasses import replace
from threading import Event

import pytest
from test_public_reference_profiles import configuration

from tether.client import Actor, ClientHost, LocalJsonlSettings, RootBusy
from tether.stores.jsonl import JsonlRoot
from tether.types.storage import StorageLimits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    loop, original_fsync = asyncio.get_running_loop(), os.fsync

    def held_fsync(descriptor):
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", held_fsync)
    operation = asyncio.create_task(host.client(Actor(id="owner")).create(spec="demo"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert await host.aclose() is False
        waiter = asyncio.create_task(host.wait_closed())
        done, _ = await asyncio.wait({waiter}, timeout=0.05)
        assert not done
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not host.drained
        with pytest.raises(RootBusy, match="local_root_already_open"):
            await ClientHost.open(config)
    finally:
        release.set()
        assert await asyncio.wait_for(host.wait_closed(), 3)
    assert host.drained
    async with await ClientHost.open(config) as reopened:
        assert len((await reopened.client(Actor(id="owner")).list()).items) == 1
