"""Browser launch cancellation ownership tests for the Apps driver."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import selected_profiles_process
from context_lifetime_controls import wait_event_or_task
from process_lifetime_controls import ShieldCancellationObserver


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


async def _exercise_browser_spawn_cancellation(
    control: _BrowserSpawnControl,
    launch_browser: Callable[[Path, dict[str, str]], Awaitable[Any]],
    tmp_path: Path,
    spawn_observer: ShieldCancellationObserver,
    cleanup_observer: ShieldCancellationObserver,
) -> None:
    control.process = _SpawnControlProcess()

    async def start_browser() -> Any:
        return await launch_browser(tmp_path, {})

    launch = asyncio.create_task(start_browser())
    spawn_observer.target = launch
    cleanup_observer.target = launch
    try:
        await wait_event_or_task(control.spawn_entered, launch)
        launch.cancel()
        await wait_event_or_task(spawn_observer.acknowledged[0], launch)
        launch.cancel()
        await wait_event_or_task(spawn_observer.acknowledged[1], launch)
        control.spawn_release.set()
        await wait_event_or_task(control.kill_sent, launch)
        launch.cancel()
        await wait_event_or_task(cleanup_observer.acknowledged[0], launch)
        assert not launch.done()
        assert control.process is not None
        control.process.stdout.feed_eof()
        control.process.stderr.feed_eof()
        with pytest.raises(asyncio.CancelledError):
            await launch
    finally:
        control.spawn_release.set()
        if control.process is not None:
            control.process.stdout.feed_eof()
            control.process.stderr.feed_eof()
            if not control.process.reaped.is_set():
                control.kill_group(control.process.pid, signal.SIGTERM)
        if not launch.done():
            launch.cancel()
        await asyncio.gather(launch, return_exceptions=True)


def test_browser_spawn_cancellation_retains_group_and_stream_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps_transport as transport

    control = _BrowserSpawnControl()
    monkeypatch.setattr(transport.shutil, "which", lambda _name: "/fake/npm")
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", control.create_process)
    monkeypatch.setattr(transport.os, "killpg", control.kill_group)
    spawn_observer = ShieldCancellationObserver(2)
    cleanup_observer = ShieldCancellationObserver(1)
    spawn_observer.install(transport, monkeypatch)
    cleanup_observer.install(selected_profiles_process, monkeypatch)

    asyncio.run(
        _exercise_browser_spawn_cancellation(
            control,
            transport._launch_browser,
            tmp_path,
            spawn_observer,
            cleanup_observer,
        )
    )
    assert control.signals == [
        (7319, signal.SIGTERM),
        (7319, signal.SIGKILL),
    ]
    assert control.process is not None
    assert control.process.returncode == -15
