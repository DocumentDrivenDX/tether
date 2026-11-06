"""Real public-client reconnect controls over an explicitly tiny feed floor."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import benchmark_reconnect as reconnect
import pytest
from benchmark_client import settings
from benchmark_profiles import PROFILES
from benchmark_reconnect import reconnect_profile

from tether.client import EntriesAppended, SessionSummary, SessionUpdated


def _summary() -> SessionSummary:
    now = datetime.now(UTC)
    return SessionSummary(
        id="fabricated-session",
        owner="fabricated-owner",
        created_at=now,
        updated_at=now,
    )


class _HeldStream:
    def __init__(self) -> None:
        self.reading = asyncio.Event()
        self.finished = False
        self.closed = False
        self.release = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.reading.set()
        try:
            await self.release.wait()
        finally:
            self.finished = True
        raise StopAsyncIteration

    async def aclose(self) -> None:
        assert self.finished
        self.closed = True


class _FailingUpdateClient:
    def __init__(self, cancel_update: bool) -> None:
        self.stream = _HeldStream()
        self.update_started = asyncio.Event()
        self.cancel_update = cancel_update

    async def snapshot(self, session_id):
        return SimpleNamespace(seq=0)

    def follow(self, *, session_id, since):
        return self.stream

    async def update(self, session_id, *, title):
        self.update_started.set()
        await self.stream.reading.wait()
        if self.cancel_update:
            await asyncio.Future()
        raise ValueError("update_primary")


def test_reconnect_reference_profiles_are_explicit_performance_workloads() -> None:
    profile = PROFILES["client"]
    assert profile.script == "benchmark_client.py"
    assert profile.profile == "local-posix-jsonl-public-client"
    assert profile.deadline_seconds == 3_600
    assert PROFILES["volatile-client"].script == profile.script
    assert PROFILES["volatile-client"].arguments == ("--storage", "volatile")


def test_reconnect_commit_validator_accepts_exact_persisted_pair() -> None:
    async def events():
        yield SessionUpdated(seq=7, summary=_summary(), last_in_commit=False)
        yield EntriesAppended(
            seq=7, session_id="fabricated-session", entries=(), last_in_commit=True
        )

    assert asyncio.run(reconnect._checked_commit(events(), 7)) == 7


@pytest.mark.parametrize("defect", ["sequence", "entries_only"])
def test_reconnect_commit_validator_refuses_incomplete_commit_variants(defect: str) -> None:
    if defect == "sequence":
        first = SessionUpdated(seq=8, summary=_summary(), last_in_commit=False)
        second = EntriesAppended(
            seq=8, session_id="fabricated-session", entries=(), last_in_commit=True
        )
        expected_error = "sequence_mismatch"
    else:
        first = EntriesAppended(
            seq=7, session_id="fabricated-session", entries=(), last_in_commit=True
        )
        second = None
        expected_error = "commit_variants_incomplete"

    async def events():
        yield first
        if second is not None:
            yield second

    with pytest.raises(RuntimeError, match=f"benchmark_reconnect_{expected_error}"):
        asyncio.run(reconnect._checked_commit(events(), 7))


def test_reconnect_profile_rejects_retention_smaller_than_missed_cursor_case() -> None:
    async def run() -> None:
        from tether.client import Actor, ClientHost, VolatileSettings

        config = settings(Path("unused"), 1, 1, storage="volatile")
        host = await ClientHost.open(config)
        try:
            client = host.client(Actor(id="fabricated-benchmark"))
            session = await client.create(spec="demo")
            with pytest.raises(ValueError, match="benchmark_reconnect_profile_invalid"):
                await reconnect_profile(client, session.id, samples=1, feed_retention=2)
            assert isinstance(config.storage, VolatileSettings)
        finally:
            await host.aclose()

    asyncio.run(run())


def test_fixture_open_failure_closes_owned_files_and_preserves_primary_error(
    monkeypatch,
) -> None:
    class Files:
        durability = "none"

        class Limits:
            feed_retention = 8

        limits = Limits()

        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> bool:
            self.closed = True
            return True

    files = Files()

    async def open_store(cls, owned_files):
        raise ValueError("store_open_primary")

    monkeypatch.setattr(reconnect.JsonlSessionStore, "open", classmethod(open_store))
    config = settings(Path("unused"), 1, 1, storage="volatile")
    with pytest.raises(ValueError, match="store_open_primary"):
        asyncio.run(
            reconnect._run_fixture(
                config, cast(reconnect.MemoryFiles, files), 1, "volatile-public-client"
            )
        )
    assert files.closed


def test_fixture_false_close_refuses_success_result() -> None:
    class Files:
        async def aclose(self) -> bool:
            return False

    class Store:
        files = Files()

        async def aclose(self) -> bool:
            return False

    with pytest.raises(RuntimeError, match="resources_not_closed"):
        asyncio.run(
            reconnect._close_fixture(
                None,
                cast(reconnect.JsonlSessionStore, Store()),
                cast(reconnect.MemoryFiles, Store.files),
            )
        )


def test_host_false_close_refuses_result_and_still_closes_store() -> None:
    class StoreFiles:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> bool:
            self.closed = True
            return True

    class Store:
        def __init__(self) -> None:
            self.files = StoreFiles()

        async def aclose(self) -> bool:
            return await self.files.aclose()

    class Host:
        async def aclose(self) -> bool:
            return False

        async def wait_closed(self) -> bool:
            return False

    store = Store()
    files = cast(reconnect.MemoryFiles, store.files)
    with pytest.raises(RuntimeError, match="host_not_closed"):
        asyncio.run(
            reconnect._close_fixture(
                cast(reconnect.ClientHost, Host()),
                cast(reconnect.JsonlSessionStore, store),
                files,
            )
        )
    assert store.files.closed


@pytest.mark.parametrize("cancel_update", [False, True])
def test_anchor_cursor_settles_waiter_before_closing_stream(cancel_update: bool) -> None:
    async def run() -> None:
        client = _FailingUpdateClient(cancel_update)
        task = asyncio.create_task(
            reconnect._anchor_cursor(cast(reconnect.ClientProtocol, client), "fabricated")
        )
        await client.update_started.wait()
        await client.stream.reading.wait()
        if cancel_update:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError, match="update_primary"):
                await task
        assert client.stream.finished
        assert client.stream.closed

    asyncio.run(run())
