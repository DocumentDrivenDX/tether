"""Retained process ownership and explicit live-observation watchdog controls."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import qualify_live_apps_process as live_process
import selected_profiles_process
from context_lifetime_controls import wait_event_or_task
from process_lifetime_controls import ShieldCancellationObserver
from qualify_live_apps import main
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_lifecycle import AppLogMonitor
from qualify_live_apps_observation import (
    LiveObservationPolicy,
    observe_until,
    use_live_observation_policy,
)
from qualify_live_apps_process import (
    _kill_and_drain,
    _kill_and_reap,
    capture_owned_command,
    spawn_owned_process,
)


async def _await_pipe_backpressure(process: asyncio.subprocess.Process) -> int:
    assert process.stdout is not None
    stream = cast(Any, process.stdout)
    while not stream._paused:
        if process.returncode is not None:
            raise AssertionError("pipe_child_terminated_before_backpressure")
        await asyncio.sleep(0)
    return len(stream._buffer)


def test_runtime_metadata_watchdog_is_typed_incomplete_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_evidence as evidence

    class Process:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False
            self.waited = False
            self.released = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self.released.wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9
            self.released.set()

        async def wait(self) -> int:
            self.waited = True
            return -9

    process = Process()
    monkeypatch.setattr(evidence.shutil, "which", lambda _name: "/fake/node")

    async def create_subprocess_exec(*_args: object, **_kwargs: object) -> Process:
        return process

    monkeypatch.setattr(evidence.asyncio, "create_subprocess_exec", create_subprocess_exec)

    with (
        use_live_observation_policy(LiveObservationPolicy(0)),
        pytest.raises(LiveQualificationIncomplete) as error,
    ):
        asyncio.run(evidence._tool_version("node", {"PATH": "/bin"}))

    assert error.value.reason == "live_runtime_metadata_not_observed"
    assert process.killed
    assert process.waited


def test_owned_cli_cleanup_survives_repeated_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.killed = False
            self.waited = False

        async def communicate(self) -> tuple[bytes, bytes]:
            self.started.set()
            await self.release.wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return self.returncode or 0

    process = Process()
    observer = ShieldCancellationObserver(1)
    observer.install(selected_profiles_process, monkeypatch)

    async def create_subprocess_exec(*_args: object, **_kwargs: object) -> Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    async def run() -> None:
        task = asyncio.create_task(
            capture_owned_command(
                "/fake/databricks",
                "apps",
                "get",
                environment={"PATH": "/bin"},
                timeout_seconds=None,
                incomplete_reason="live_admin_command_not_completed",
            )
        )
        observer.target = task
        try:
            await wait_event_or_task(process.started, task)
            task.cancel()
            await wait_event_or_task(observer.entered, task)
            task.cancel()
            await wait_event_or_task(observer.acknowledged[0], task)
            assert not task.done()
            process.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            process.release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert process.killed
    assert process.waited


def test_owned_spawn_is_killed_and_reaped_after_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.stdout = asyncio.StreamReader()
            self.killed = False
            self.waited = False
            self.drained = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()

        async def wait(self) -> int:
            self.waited = True
            return -9

        async def communicate(self) -> tuple[bytes, bytes]:
            self.drained = True
            return await self.stdout.read(), b""

    process: Process | None = None
    spawn_entered = asyncio.Event()
    release_spawn = asyncio.Event()
    outer_observer = ShieldCancellationObserver(2)
    outer_observer.install(live_process, monkeypatch)

    async def create_subprocess_exec(*_args: object, **_kwargs: object) -> Process:
        spawn_entered.set()
        await release_spawn.wait()
        assert process is not None
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    async def run() -> None:
        nonlocal process
        process = Process()
        task = asyncio.create_task(
            spawn_owned_process("/fake/databricks", "apps", "logs", environment={"PATH": "/bin"})
        )
        outer_observer.target = task
        try:
            await wait_event_or_task(spawn_entered, task)
            task.cancel()
            await wait_event_or_task(outer_observer.acknowledged[0], task)
            task.cancel()
            await wait_event_or_task(outer_observer.acknowledged[1], task)
            release_spawn.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release_spawn.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert process is not None
    assert process.killed
    assert process.waited
    assert process.drained


def test_owned_process_reap_drains_pipe_pressure() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('writing\\n'); sys.stderr.flush(); "
            "sys.stdout.buffer.write(b'x' * (64 * 1024 * 1024)); sys.stdout.flush(); "
            "sys.stdin.buffer.read()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stderr is not None
            assert await process.stderr.readline() == b"writing\n"
            buffered = await _await_pipe_backpressure(process)
            assert buffered > 0
            assert process.returncode is None
        finally:
            await _kill_and_reap(process)
        assert process.returncode is not None

    asyncio.run(run())


def test_failed_communication_replaces_reader_before_reaping_pipe_child() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "sys.stderr.write('writing\\n'); sys.stderr.flush(); "
            "sys.stdout.buffer.write(b'x' * (64 * 1024 * 1024)); sys.stdout.flush()",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communication: asyncio.Task[tuple[bytes | None, bytes | None]] | None = None
        try:
            assert process.stderr is not None
            assert await process.stderr.readline() == b"writing\n"
            buffered = await _await_pipe_backpressure(process)
            assert buffered > 0

            async def failed_communication() -> tuple[bytes | None, bytes | None]:
                raise RuntimeError("controlled_communication_failure")

            communication = asyncio.create_task(failed_communication())
            await asyncio.gather(communication, return_exceptions=True)
            await _kill_and_drain(process, communication)
        finally:
            if communication is not None and not communication.done():
                communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)
            if process.returncode is None:
                await _kill_and_reap(process)
        assert process.returncode is not None

    asyncio.run(run())


def test_observation_preserves_body_timeout_without_policy() -> None:
    async def run() -> None:
        with pytest.raises(TimeoutError, match="body timeout"):
            async with observe_until("not-a-watchdog"):
                raise TimeoutError("body timeout")

    asyncio.run(run())


def test_observation_preserves_body_timeout_with_policy() -> None:
    async def run() -> None:
        with (
            use_live_observation_policy(LiveObservationPolicy(60)),
            pytest.raises(TimeoutError, match="body timeout"),
        ):
            async with observe_until("not-a-watchdog"):
                raise TimeoutError("body timeout")

    asyncio.run(run())


def test_observation_classifies_only_its_expired_watchdog_as_incomplete() -> None:
    async def run() -> None:
        with (
            use_live_observation_policy(LiveObservationPolicy(0)),
            pytest.raises(LiveQualificationIncomplete) as error,
        ):
            async with observe_until("live_app_state_not_observed"):
                await asyncio.sleep(0)
        assert error.value.reason == "live_app_state_not_observed"

    asyncio.run(run())


def test_log_monitor_replaces_failed_reader_and_reaps_pipe_pressure() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "sys.stderr.write('ready\\n'); sys.stderr.flush(); "
            "sys.stdout.buffer.write(b'x' * (64 * 1024 * 1024)); sys.stdout.flush()",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            assert process.stderr is not None
            assert await process.stderr.readline() == b"ready\n"
            assert await _await_pipe_backpressure(process) > 0

            async def failed_reader() -> None:
                raise RuntimeError("controlled_reader_failure")

            monitor = AppLogMonitor(
                process=process,
                invocation_id="fabricated-invocation",
                worker_started=asyncio.Event(),
                report=asyncio.get_running_loop().create_future(),
                reader=asyncio.create_task(failed_reader()),
            )
            reader = monitor.reader
            assert reader is not None
            await asyncio.gather(reader, return_exceptions=True)
            with pytest.raises(LiveQualificationIncomplete):
                await monitor.aclose()
            assert process.returncode is not None
            assert monitor.discard_reader is not None
            assert monitor.discard_reader.done()
        finally:
            if process.returncode is None:
                await _kill_and_reap(process)

    asyncio.run(run())


def test_live_entrypoint_reports_observation_exhaustion_as_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import qualify_live_apps as live

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_live_apps",
            "--profile",
            "p",
            "--admin-profile",
            "a",
            "--output",
            str(tmp_path / "receipt"),
        ],
    )

    async def incomplete(*_args: object, **_kwargs: object) -> None:
        raise LiveQualificationIncomplete("live_app_state_not_observed")

    monkeypatch.setattr(live, "_run", incomplete)
    assert main() == 2
    assert (
        capsys.readouterr().err.strip() == "Live Apps check incomplete: live_app_state_not_observed"
    )


def test_live_entrypoint_applies_explicit_observation_watchdog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live
    from qualify_live_apps_observation import observation_watchdog_seconds

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_live_apps",
            "--profile",
            "p",
            "--admin-profile",
            "a",
            "--output",
            str(tmp_path / "receipt"),
            "--observation-watchdog-seconds",
            "30",
        ],
    )

    async def inspect_policy(*_args: object, **_kwargs: object) -> None:
        assert observation_watchdog_seconds() == 30

    monkeypatch.setattr(live, "_run", inspect_policy)
    assert main() == 0
