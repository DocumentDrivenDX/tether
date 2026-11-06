"""Browser launch cancellation ownership tests for the Apps driver."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest


class _SpawnControlProcess:
    pid = 7319

    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.reaped = asyncio.Event()

    async def wait(self) -> int:
        await self.reaped.wait()
        return self.returncode or 0


class _BrowserSpawnControl:
    def __init__(self) -> None:
        self.process: _SpawnControlProcess | None = None
        self.spawn_entered = asyncio.Event()
        self.spawn_release = asyncio.Event()
        self.kill_sent = asyncio.Event()
        self.signals: list[tuple[int, int]] = []

    async def create_process(self, *_args: object, **_kwargs: object) -> _SpawnControlProcess:
        self.spawn_entered.set()
        await self.spawn_release.wait()
        assert self.process is not None
        return self.process

    def kill_group(self, pgid: int, selected_signal: int) -> None:
        assert self.process is not None
        self.signals.append((pgid, selected_signal))
        if selected_signal == signal.SIGTERM:
            self.process.returncode = -15
            self.process.reaped.set()
        elif selected_signal == signal.SIGKILL:
            self.kill_sent.set()


def test_browser_spawn_cancellation_retains_group_and_stream_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps_transport as transport

    control = _BrowserSpawnControl()
    monkeypatch.setattr(transport.shutil, "which", lambda _name: "/fake/npm")
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", control.create_process)
    monkeypatch.setattr(transport.os, "killpg", control.kill_group)

    async def run() -> None:
        control.process = _SpawnControlProcess()
        launch = asyncio.create_task(transport._launch_browser(tmp_path, {}))
        await control.spawn_entered.wait()
        launch.cancel()
        await asyncio.sleep(0)
        launch.cancel()
        control.spawn_release.set()
        await control.kill_sent.wait()
        launch.cancel()
        assert not launch.done()
        assert control.process is not None
        control.process.stdout.feed_eof()
        control.process.stderr.feed_eof()
        with pytest.raises(asyncio.CancelledError):
            await launch

    asyncio.run(run())
    assert control.signals == [
        (7319, signal.SIGTERM),
        (7319, signal.SIGKILL),
    ]
    assert control.process is not None
    assert control.process.returncode == -15
