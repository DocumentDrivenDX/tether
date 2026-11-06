"""Remote-probe terminal classification, remote-command watch and cleanup ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
import web_remote_probe as probe
from process import CommandIncompleteError
from web_remote_capture import StderrDrain

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _finished(value: Any = None, *, error: BaseException | None = None) -> Any:
    future: Any = asyncio.get_running_loop().create_future()
    if error is not None:
        future.set_exception(error)
        future.exception()  # mark retrieved
    else:
        future.set_result(value)
    return future


def _pending() -> Any:
    return asyncio.get_running_loop().create_future()


async def _classify(
    *,
    stop: bool = False,
    overflow: bool = False,
    stderr: Any = None,
    exited: bool = False,
    include_stop_waiter: bool = True,
) -> Exception | None:
    stop_waiter: Any = None
    if include_stop_waiter:
        stop_waiter = await _finished(True) if stop else _pending()
    overflow_waiter: Any = await _finished(True) if overflow else _pending()
    stderr_task: Any = stderr if stderr is not None else _pending()
    process_exit: Any = await _finished(0) if exited else _pending()
    watched = [t for t in (stop_waiter, overflow_waiter, stderr_task, process_exit) if t]
    done = {t for t in watched if t.done()}
    return probe._terminal_reason(
        cast(Any, done),
        process_exit=process_exit,
        stderr_task=stderr_task,
        overflow_waiter=overflow_waiter,
        stop_waiter=stop_waiter,
        stage="unit",
    )


def _closed(error: Exception | None) -> tuple[str, str] | str | None:
    if isinstance(error, CommandIncompleteError):
        return error.reason, error.phase
    return None if error is None else str(error)


async def test_terminal_reason_orders_observer_stop_before_every_other_signal() -> None:
    result = await _classify(stop=True, overflow=True, exited=True)
    assert _closed(result) == ("watchdog_exhausted", "execution")
    assert _closed(await _classify(overflow=True, exited=True)) == (
        "output_capture_failed",
        "execution",
    )


async def test_terminal_reason_classifies_stderr_outcomes() -> None:
    clean = await _finished(StderrDrain(10, False, False))
    assert _closed(await _classify(stderr=clean)) == "installed_api_server_stderr_closed_unit"
    overflowed = await _finished(StderrDrain(10, True, False))
    assert _closed(await _classify(stderr=overflowed)) == ("output_capture_failed", "execution")
    failed_read = await _finished(StderrDrain(1, False, True))
    assert _closed(await _classify(stderr=failed_read)) == ("output_capture_failed", "execution")
    raised = await _finished(error=OSError("fabricated"))
    assert _closed(await _classify(stderr=raised)) == ("output_capture_failed", "execution")
    cancelled = _pending()
    cancelled.cancel()
    assert _closed(await _classify(stderr=cancelled)) == ("output_capture_failed", "execution")


async def test_terminal_reason_for_exit_and_for_nothing_terminal() -> None:
    assert _closed(await _classify(exited=True)) == "installed_api_server_exited_unit"
    assert _closed(await _classify(include_stop_waiter=False)) is None


class _Process:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.exit_event = asyncio.Event()
        self.killed = False

    async def wait(self) -> int:
        await self.exit_event.wait()
        return int(self.returncode or 0)

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        self.exit_event.set()


class _GoneProcess(_Process):
    def kill(self) -> None:
        raise ProcessLookupError


async def test_ready_state_failure_reports_exit_before_readiness_without_tasks() -> None:
    exited = cast(Any, _Process(returncode=3))
    failure = probe._ready_state_failure(exited, await _finished(3), _pending(), _pending(), None)
    assert _closed(failure) == "installed_api_server_exited_before_readiness"
    running = cast(Any, _Process())
    assert probe._ready_state_failure(running, _pending(), _pending(), _pending(), None) is None
    stop = await _finished(True)
    failure = probe._ready_state_failure(running, _pending(), _pending(), _pending(), stop)
    assert _closed(failure) == ("watchdog_exhausted", "execution")


def test_python_in_selects_platform_interpreter(tmp_path: Path) -> None:
    assert probe.python_in(tmp_path).parent == tmp_path / "bin"
    assert probe.python_in(tmp_path).name == "python"


async def test_kill_and_reap_tolerates_already_gone_process() -> None:
    process = _Process()
    await probe._kill_and_reap(cast(Any, process))
    assert process.killed
    gone = _GoneProcess(returncode=0)
    gone.exit_event.set()
    await probe._kill_and_reap(cast(Any, gone))


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.closed = 0
        self.fail = fail

    async def aclose(self) -> None:
        self.closed += 1
        if self.fail:
            raise RuntimeError("fabricated close failure")


async def test_close_helpers_accept_missing_client_and_report_cancellation() -> None:
    await probe._close_client(None)
    assert await probe._close_client_retained(None) is False
    client = _Client()
    assert await probe._close_client_retained(cast(Any, client)) is False
    assert client.closed == 1


async def test_cleanup_probe_stops_server_and_keeps_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[str] = []

    async def stop_failing(*_args: object) -> None:
        stopped.append("stop")
        raise CommandIncompleteError("output_capture_failed", "execution")

    monkeypatch.setattr(probe, "_stop_server", stop_failing)
    client = _Client(fail=True)
    with pytest.raises(CommandIncompleteError) as caught:
        await probe._cleanup_probe(
            cast(Any, _Process()), _pending(), asyncio.Event(), cast(Any, client)
        )
    assert caught.value.reason == "output_capture_failed"
    assert (stopped, client.closed) == (["stop"], 1)

    async def stop_ok(*_args: object) -> None:
        return None

    monkeypatch.setattr(probe, "_stop_server", stop_ok)
    with pytest.raises(RuntimeError, match="fabricated close failure"):
        await probe._cleanup_probe(
            cast(Any, _Process()), _pending(), asyncio.Event(), cast(Any, _Client(fail=True))
        )
    await probe._cleanup_probe(cast(Any, _Process()), _pending(), asyncio.Event(), None)


async def test_cancel_probe_closes_client_of_finished_probe_and_ignores_failed_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probe, "RemoteClient", _Client)
    client = _Client()

    async def build() -> Any:
        return client

    done = asyncio.create_task(build())
    await done
    await probe._cancel_probe(cast(Any, done))
    assert client.closed == 1

    async def refuse() -> Any:
        raise RuntimeError("fabricated probe failure")

    failed = asyncio.create_task(refuse())
    await asyncio.gather(failed, return_exceptions=True)
    await probe._cancel_probe(cast(Any, failed))
    assert client.closed == 1


async def _remote_command(
    *,
    checked: Any,
    server: Any,
    stderr_task: Any,
    overflow: asyncio.Event,
    policy: probe.ObservationPolicy | None,
    tmp_path: Path,
) -> None:
    await probe._run_remote_command(
        checked, tmp_path, 1234, "challenge", "nonce", server, stderr_task, overflow, policy
    )


async def test_remote_command_success_runs_exact_isolated_program(tmp_path: Path) -> None:
    seen: list[tuple[list[str], Path]] = []

    async def checked(command: list[str], directory: Path) -> str:
        seen.append((command, directory))
        return ""

    await _remote_command(
        checked=checked,
        server=_Process(),
        stderr_task=_pending(),
        overflow=asyncio.Event(),
        policy=None,
        tmp_path=tmp_path,
    )
    command, directory = seen[0]
    assert directory == tmp_path
    assert command[0] == str(probe.python_in(tmp_path / "remote"))
    assert command[1] == "-I"
    assert command[-3:] == ["http://127.0.0.1:1234", "challenge", "nonce"]


async def _hanging_check(_command: list[str], _directory: Path) -> str:
    await asyncio.Event().wait()
    return ""


async def test_remote_command_stops_on_observer_overflow_server_exit_and_stderr_close(
    tmp_path: Path,
) -> None:
    stop = asyncio.Event()
    stop.set()
    with pytest.raises(CommandIncompleteError) as caught:
        await _remote_command(
            checked=_hanging_check,
            server=_Process(),
            stderr_task=_pending(),
            overflow=asyncio.Event(),
            policy=probe.ObservationPolicy(stop=stop),
            tmp_path=tmp_path,
        )
    assert caught.value.reason == "watchdog_exhausted"

    overflow = asyncio.Event()
    overflow.set()
    with pytest.raises(CommandIncompleteError) as caught:
        await _remote_command(
            checked=_hanging_check,
            server=_Process(),
            stderr_task=_pending(),
            overflow=overflow,
            policy=None,
            tmp_path=tmp_path,
        )
    assert caught.value.reason == "output_capture_failed"

    server = _Process(returncode=1)
    server.exit_event.set()
    with pytest.raises(ValueError, match="installed_api_server_exited_during_remote_contract"):
        await _remote_command(
            checked=_hanging_check,
            server=server,
            stderr_task=_pending(),
            overflow=asyncio.Event(),
            policy=None,
            tmp_path=tmp_path,
        )

    closed = await _finished(StderrDrain(0, False, False))
    with pytest.raises(ValueError, match="installed_api_server_stderr_closed_during_remote"):
        await _remote_command(
            checked=_hanging_check,
            server=_Process(),
            stderr_task=closed,
            overflow=asyncio.Event(),
            policy=None,
            tmp_path=tmp_path,
        )


async def test_remote_command_failure_propagates_after_cleanup(tmp_path: Path) -> None:
    async def failing(_command: list[str], _directory: Path) -> str:
        raise ValueError("fabricated_remote_contract_failure")

    with pytest.raises(ValueError, match="fabricated_remote_contract_failure"):
        await _remote_command(
            checked=failing,
            server=_Process(),
            stderr_task=_pending(),
            overflow=asyncio.Event(),
            policy=None,
            tmp_path=tmp_path,
        )
