"""Seed-process startup and cleanup stay owned across cancellation and reader failure."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest

SERVER_DIRECTORY = Path(__file__).parent / "ui"
SERVER_PATH = SERVER_DIRECTORY / "server.py"


def _server(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(SERVER_DIRECTORY))
    spec = importlib.util.spec_from_file_location("tether_browser_seed_lifecycle", SERVER_PATH)
    if spec is None or spec.loader is None:
        pytest.fail("browser_fixture_server_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


class _Reader:
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.calls = 0
        self.fail_first = False

    async def read(self, _size: int) -> bytes:
        self.calls += 1
        self.started.set()
        if self.fail_first and self.calls == 1:
            await self.closed.wait()
            raise OSError("fabricated_reader_failure")
        await self.closed.wait()
        self.finished.set()
        return b""


class _Process:
    def __init__(self, reader: _Reader) -> None:
        self.stdout = reader
        self.returncode: int | None = None
        self.killed = asyncio.Event()
        self.wait_started = asyncio.Event()
        self.reap = asyncio.Event()
        self.reaped = False

    def kill(self) -> None:
        self.killed.set()
        self.returncode = -9
        self.stdout.closed.set()

    async def wait(self) -> int:
        self.wait_started.set()
        await self.reap.wait()
        self.reaped = True
        return self.returncode or 0


def test_spawn_cancellation_retains_spawn_and_stops_exact_seed_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(monkeypatch)

    async def run() -> None:
        spawn_entered = asyncio.Event()
        allow_spawn = asyncio.Event()
        reader = _Reader()
        process = _Process(reader)

        async def delayed_spawn(*_args: object, **_kwargs: object) -> _Process:
            spawn_entered.set()
            await allow_spawn.wait()
            process.reap.set()
            return process

        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", delayed_spawn)
        task = asyncio.create_task(
            server._spawn_seed_process(
                tmp_path, tmp_path / "ready", asyncio.get_running_loop().time() + 100
            )
        )
        await spawn_entered.wait()
        task.cancel()
        allow_spawn.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed.is_set()
        assert process.reaped
        assert reader.finished.is_set()

    asyncio.run(run())


def test_repeated_cancellation_waits_for_seed_reap_and_output_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(monkeypatch)

    async def run() -> None:
        reader = _Reader()
        process = _Process(reader)
        ready = asyncio.get_running_loop().create_future()
        process_done = asyncio.create_task(process.wait())
        output_drain = asyncio.create_task(server._drain_seed_output(reader, ready))
        cleanup = asyncio.create_task(
            server._settle_seed_process(process, process_done, output_drain)
        )
        await process.killed.wait()
        await process.wait_started.wait()
        await reader.started.wait()
        cleanup.cancel()
        cleanup.cancel()
        process.reap.set()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        assert process.reaped
        assert reader.finished.is_set()
        assert process_done.done()
        assert output_drain.done()

    asyncio.run(run())


def test_failed_output_reader_is_replaced_before_process_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(monkeypatch)

    async def run() -> None:
        reader = _Reader()
        reader.fail_first = True
        process = _Process(reader)
        process.reap.set()
        process_done = asyncio.create_task(process.wait())
        ready = asyncio.get_running_loop().create_future()
        output_drain = asyncio.create_task(server._drain_seed_output(reader, ready))
        with pytest.raises(
            server.BrowserFixtureIncomplete,
            match="browser_fixture_output_reader_failed",
        ):
            await server._stop_seed_process(process, process_done, output_drain)
        assert process.killed.is_set()
        assert process.reaped
        assert reader.calls == 2
        assert reader.finished.is_set()

    asyncio.run(run())
