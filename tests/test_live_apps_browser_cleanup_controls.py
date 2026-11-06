"""Pipe-pressure and retained-cleanup controls for the opt-in browser owner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from qualify_live_apps import _qualify_unknown_effect, _run_case
from qualify_live_apps_config import LiveSettings
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_transport import LiveBrowserChild, _stop_browser_group
from selected_profiles_process import await_retained


class _ObservedReader:
    def __init__(self, reader: asyncio.StreamReader, drained: asyncio.Event) -> None:
        self.reader = reader
        self.drained = drained

    async def read(self, size: int) -> bytes:
        chunk = await self.reader.read(size)
        if chunk:
            self.drained.set()
        return chunk


class _DrainGatedProcess:
    def __init__(self, process: asyncio.subprocess.Process, drained: asyncio.Event) -> None:
        self.process = process
        self.pid = process.pid
        self.drained = drained
        self.stdout = _ObservedReader(cast(asyncio.StreamReader, process.stdout), drained)
        self.stderr = process.stderr

    async def wait(self) -> int:
        await self.drained.wait()
        return await self.process.wait()


async def _await_pipe_pressure(
    process: asyncio.subprocess.Process, reader: asyncio.StreamReader
) -> bool:
    reader_state = cast(Any, reader)
    transport = reader_state._transport
    while True:
        if reader_state._buffer and transport._paused:
            return True
        if reader.at_eof() or process.returncode is not None:
            return False
        await asyncio.sleep(0)


async def _kill_and_drain_fixture_process(process: asyncio.subprocess.Process) -> None:
    async def cleanup() -> None:
        communication = asyncio.create_task(process.communicate())
        await asyncio.sleep(0)
        if process.returncode is None:
            process.kill()
        await communication

    await await_retained(asyncio.create_task(cleanup()))


def test_failed_capture_drain_starts_before_reaping_pipe_pressured_child() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import os; payload=b'x'*65536;\nwhile True: os.write(1, payload)",
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            assert await _await_pipe_pressure(process, process.stdout)

            async def failed_capture() -> tuple[bytes, bool]:
                raise RuntimeError("fabricated_reader_failure")

            failed = asyncio.create_task(failed_capture())
            await asyncio.sleep(0)
            assert failed.done()
            failed.exception()
            drained = asyncio.Event()
            owned = _DrainGatedProcess(process, drained)
            await _stop_browser_group(cast(asyncio.subprocess.Process, owned), (failed,))
            assert drained.is_set()
            assert process.returncode is not None
        finally:
            await _kill_and_drain_fixture_process(process)

    asyncio.run(run())


def test_failed_replacement_drain_reports_closed_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_transport as transport

    class Reader:
        async def read(self, _size: int) -> bytes:
            return b""

    class Process:
        pid = 9042
        stdout = Reader()
        stderr = Reader()
        returncode = 0

        async def wait(self) -> int:
            return self.returncode

    process = Process()
    monkeypatch.setattr(transport.os, "killpg", lambda *_args: None)
    failed_calls: list[object] = []

    async def failed_drain(_reader: object) -> None:
        failed_calls.append(_reader)
        raise RuntimeError("private canary must not escape")

    async def failed_capture() -> tuple[bytes, bool]:
        raise RuntimeError("capture failed")

    monkeypatch.setattr(transport, "_discard_output", failed_drain)

    async def run() -> None:
        capture = asyncio.create_task(failed_capture())
        with pytest.raises(LiveQualificationIncomplete) as caught:
            await _stop_browser_group(cast(Any, process), (capture,))
        assert caught.value.reason == "live_browser_output_not_settled"

    asyncio.run(run())
    assert len(failed_calls) == 2


def test_cleanup_incomplete_prevents_passed_live_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live
    import qualify_live_apps_cleanup as cleanup

    class Reader:
        async def read(self, _size: int) -> bytes:
            return b""

    class Process:
        pid = 9043
        stdout = Reader()
        stderr = Reader()
        returncode = 0

        async def wait(self) -> int:
            return self.returncode

    client, http, monitor, browser = _CloseControl(), _CloseControl(), _CloseControl(), Process()
    writes: list[object] = []

    profile = AsyncMock(return_value=(object(), "Bearer fabricated"))
    open_client = AsyncMock(return_value=(client, http))
    launch = AsyncMock(return_value=LiveBrowserChild(cast(Any, browser), ()))
    unknown = AsyncMock(return_value=(client, http, monitor, {"complete": True}))
    receipt = AsyncMock(side_effect=lambda *_args, **_kwargs: writes.append(object()))
    failed_stop = AsyncMock(
        side_effect=LiveQualificationIncomplete("live_browser_output_not_settled")
    )
    monkeypatch.setattr(live, "inventory", lambda _root: {"current": "digest"})
    monkeypatch.setattr(live, "_profile_credential", profile)
    monkeypatch.setattr(live, "_client_for", open_client)
    monkeypatch.setattr(live, "_launch_browser", launch)
    monkeypatch.setattr(live, "_qualify_shared_session", AsyncMock())
    monkeypatch.setattr(live, "_qualify_unknown_effect", unknown)
    monkeypatch.setattr(live, "write_live_receipt", receipt)
    monkeypatch.setattr(cleanup, "_stop_browser_group", failed_stop)

    with pytest.raises(LiveQualificationIncomplete):
        asyncio.run(
            _run_case(
                "caller",
                "admin",
                None,
                {},
                LiveSettings("app", "https://apps.example", "", "/api/v1"),
                tmp_path / "receipt.json",
                False,
            )
        )
    assert writes == []


class _CloseControl:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("cancel_cleanup", [False, True], ids=["incomplete", "repeat-cancel"])
def test_unknown_effect_incomplete_waits_for_retained_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch, cancel_cleanup: bool
) -> None:
    import qualify_live_apps as live
    import qualify_live_apps_cleanup as cleanup

    client, http, monitor = _CloseControl(), _CloseControl(), _CloseControl()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def prepare(*_args: object) -> tuple[dict[str, Any], object]:
        return {"session_id": "s", "invocation_id": "i"}, monitor

    async def restart(*_args: object) -> dict[str, Any]:
        raise LiveQualificationIncomplete("live_app_state_not_observed")

    async def held_close(
        owned_client: object, owned_http: object, owned_monitor: object, *_args: object
    ) -> None:
        entered.set()
        await release.wait()
        for resource in (owned_client, owned_http, owned_monitor):
            await cast(Any, resource).aclose()

    monkeypatch.setattr(live, "_prepare_unknown_effect", prepare)
    monkeypatch.setattr(live, "_restart_unknown_effect", restart)
    monkeypatch.setattr(cleanup, "cleanup_case_resources", held_close)

    async def run() -> None:
        task = asyncio.create_task(
            _qualify_unknown_effect(
                cast(Any, client),
                cast(Any, http),
                cast(Any, object()),
                "admin",
                None,
                {},
                LiveSettings("app", "https://apps.example", "", "/api/v1"),
                False,
                cast(Any, object()),
                cast(Any, object()),
            )
        )
        entered_wait = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait({entered_wait, task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            entered_wait.cancel()
            await task
        await entered_wait
        if cancel_cleanup:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done()
        release.set()
        if cancel_cleanup:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(LiveQualificationIncomplete):
                await task

    asyncio.run(run())
    assert client.closed
    assert http.closed
    assert monitor.closed
