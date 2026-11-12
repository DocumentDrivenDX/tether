"""Tooling subprocess ownership helpers over fabricated children and process groups."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import process as proc
import pytest
from context_lifetime_controls import wait_event_or_task
from process import CommandIncompleteError, run_command
from selected_profiles_process import await_retained

pytestmark = pytest.mark.anyio

PGID = 543210


async def _settle_tasks(*tasks: asyncio.Future[Any]) -> None:
    async def settle() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


class _ShieldObserver:
    def __init__(self) -> None:
        self._asyncio = asyncio
        self.calls = 0
        self.entered = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.cancel_ack = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._asyncio, name)

    async def shield(self, future: asyncio.Future[Any]) -> Any:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
        elif self.calls == 2:
            self.second_entered.set()
        try:
            return await self._asyncio.shield(future)
        except asyncio.CancelledError:
            if self.calls == 1:
                asyncio.get_running_loop().call_soon(self.cancel_ack.set)
            raise


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Child:
    def __init__(
        self,
        *,
        returncode: int | None = 0,
        stdout: bytes | None = b"out",
        stderr: bytes | None = b"err",
        communicate_error: BaseException | None = None,
        wait_error: BaseException | None = None,
        communicate_override: Any = None,
    ) -> None:
        self._override = communicate_override
        self.pid = PGID
        self.returncode = returncode
        self._streams = (stdout, stderr)
        self._communicate_error = communicate_error
        self._wait_error = wait_error
        self.killed = 0
        self.waited = 0

    def kill(self) -> None:
        self.killed += 1

    async def wait(self) -> int:
        self.waited += 1
        if self._wait_error is not None:
            raise self._wait_error
        return int(self.returncode or 0)

    async def communicate(self) -> tuple[bytes | None, bytes | None]:
        if self._override is not None:
            return await self._override()
        if self._communicate_error is not None:
            raise self._communicate_error
        return self._streams


def _process(child: _Child) -> asyncio.subprocess.Process:
    return cast(asyncio.subprocess.Process, child)


def _reason(error: pytest.ExceptionInfo[CommandIncompleteError]) -> tuple[str, str]:
    return error.value.reason, error.value.phase


def _killpg(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    def fake(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        behavior(sig)

    monkeypatch.setattr(proc.os, "killpg", fake)
    return calls


def _absent(_sig: int) -> None:
    raise ProcessLookupError


def _denied(_sig: int) -> None:
    raise PermissionError


def _present(_sig: int) -> None:
    return None


async def test_retained_waits_through_cancellation_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    observer = _ShieldObserver()
    monkeypatch.setattr(proc, "asyncio", observer)

    async def work() -> str:
        started.set()
        await gate.wait()
        return "done"

    task = asyncio.create_task(work())
    waiter = asyncio.create_task(proc._retained(task))
    try:
        await wait_event_or_task(started, waiter)
        await wait_event_or_task(observer.entered, waiter)
        waiter.cancel()
        await wait_event_or_task(observer.cancel_ack, waiter)
        assert not waiter.done()
        gate.set()
        value, cancelled = await waiter
        assert (value, cancelled) == ("done", True)
        assert await proc._retained(task) == ("done", False)
    finally:
        gate.set()
        await _settle_tasks(waiter, task)


def test_signal_owned_group_and_single_child_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _killpg(monkeypatch, _present)
    child = _Child(returncode=None)
    proc._signal_owned(_process(child), True)
    assert calls == [(PGID, proc.signal.SIGKILL)]
    assert child.killed == 0
    proc._signal_owned(_process(child), False)
    assert child.killed == 1
    exited = _Child(returncode=0)
    proc._signal_owned(_process(exited), False)
    assert exited.killed == 0
    _killpg(monkeypatch, _absent)
    proc._signal_owned(_process(child), True)
    _killpg(monkeypatch, _denied)
    with pytest.raises(CommandIncompleteError) as error:
        proc._signal_owned(_process(child), True)
    assert _reason(error) == ("cleanup_unconfirmed", "cleanup")


def test_group_exists_reports_presence_absence_and_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _killpg(monkeypatch, _present)
    assert proc._group_exists(PGID) is True
    _killpg(monkeypatch, _absent)
    assert proc._group_exists(PGID) is False
    _killpg(monkeypatch, _denied)
    with pytest.raises(CommandIncompleteError):
        proc._group_exists(PGID)


async def test_group_absent_polls_until_group_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    remaining = [True, True]

    def behavior(_sig: int) -> None:
        if remaining:
            remaining.pop()
            return
        raise ProcessLookupError

    calls = _killpg(monkeypatch, behavior)
    await proc._group_absent(PGID)
    assert len(calls) == 3
    _killpg(monkeypatch, _denied)
    with pytest.raises(CommandIncompleteError):
        await proc._group_absent(PGID)


async def test_wait_for_streams_reports_completion() -> None:
    done = asyncio.create_task(_Child().communicate())
    assert await proc._wait_for_streams(done, None) is True
    gate = asyncio.Event()

    async def blocked() -> tuple[bytes | None, bytes | None]:
        await gate.wait()
        return None, None

    pending = asyncio.create_task(blocked())
    assert await proc._wait_for_streams(pending, 0.001) is False
    gate.set()
    await pending


async def test_cleanup_failure_modes_are_all_closed_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _killpg(monkeypatch, _absent)
    clean = _Child()
    await proc._cleanup(_process(clean), asyncio.create_task(clean.communicate()), True)
    assert clean.waited == 1

    broken_wait = _Child(wait_error=RuntimeError("fabricated"))
    with pytest.raises(CommandIncompleteError) as error:
        await proc._cleanup(
            _process(broken_wait), asyncio.create_task(broken_wait.communicate()), False
        )
    assert _reason(error) == ("cleanup_unconfirmed", "cleanup")

    failed_streams = _Child(communicate_error=OSError("fabricated"))
    with pytest.raises(CommandIncompleteError) as error:
        await proc._cleanup(
            _process(failed_streams), asyncio.create_task(failed_streams.communicate()), False
        )
    assert _reason(error) == ("cleanup_unconfirmed", "cleanup")

    _killpg(monkeypatch, _denied)
    denied = _Child()
    with pytest.raises(CommandIncompleteError):
        await proc._cleanup(_process(denied), asyncio.create_task(denied.communicate()), True)


async def test_settle_reraises_cancellation_after_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    entered = asyncio.Event()
    finished: list[bool] = []
    observer = _ShieldObserver()
    monkeypatch.setattr(proc, "asyncio", observer)

    async def slow_cleanup(*_args: object) -> None:
        entered.set()
        await release.wait()
        finished.append(True)

    monkeypatch.setattr(proc, "_cleanup", slow_cleanup)
    child = _Child()
    settle = asyncio.create_task(
        proc._settle(_process(child), asyncio.create_task(child.communicate()), False)
    )
    try:
        await wait_event_or_task(entered, settle)
        await wait_event_or_task(observer.entered, settle)
        settle.cancel()
        await wait_event_or_task(observer.cancel_ack, settle)
        assert not settle.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await settle
        assert finished == [True]
    finally:
        release.set()
        await _settle_tasks(settle)


async def test_spawn_failures_are_closed_dispatch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for failure in (OSError("fabricated"), ValueError("fabricated")):

        async def fail(*_args: object, _failure: Exception = failure, **_kwargs: object) -> None:
            raise _failure

        monkeypatch.setattr(proc.asyncio, "create_subprocess_exec", fail)
        with pytest.raises(CommandIncompleteError) as error:
            await run_command([sys.executable, "-c", "pass"])
        assert _reason(error) == ("dispatch_failed", "spawn")


async def test_cancelled_spawn_reaps_the_child_it_started(monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    child = _Child()
    observer = _ShieldObserver()
    monkeypatch.setattr(proc, "asyncio", observer)

    async def slow_spawn(*_args: object, **_kwargs: object) -> _Child:
        started.set()
        await release.wait()
        return child

    monkeypatch.setattr(proc.asyncio, "create_subprocess_exec", slow_spawn)
    _killpg(monkeypatch, _absent)
    task = asyncio.create_task(proc._spawn_command(["/bin/true"], None, None, True))
    try:
        await wait_event_or_task(started, task)
        task.cancel()
        await wait_event_or_task(observer.cancel_ack, task)
        await wait_event_or_task(observer.second_entered, task)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.waited == 1
    finally:
        release.set()
        await _settle_tasks(task)


async def test_cancelled_spawn_that_then_fails_is_dispatch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    observer = _ShieldObserver()
    monkeypatch.setattr(proc, "asyncio", observer)

    async def failing_spawn(*_args: object, **_kwargs: object) -> None:
        started.set()
        await release.wait()
        raise OSError("fabricated")

    monkeypatch.setattr(proc.asyncio, "create_subprocess_exec", failing_spawn)
    task = asyncio.create_task(proc._spawn_command(["/bin/true"], None, None, False))
    try:
        await wait_event_or_task(started, task)
        task.cancel()
        await wait_event_or_task(observer.cancel_ack, task)
        await wait_event_or_task(observer.second_entered, task)
        release.set()
        with pytest.raises(CommandIncompleteError) as error:
            await task
        assert _reason(error) == ("dispatch_failed", "spawn")
    finally:
        release.set()
        await _settle_tasks(task)


async def test_complete_command_decodes_replacement_characters_and_missing_streams() -> None:
    result = await proc._complete_command(
        _process(_Child(returncode=7, stdout=b"ok\xff", stderr=None)), None, False
    )
    assert result == proc.Result(7, "ok�", "")


@pytest.mark.parametrize(
    ("child", "reason", "phase"),
    [
        (_Child(communicate_error=RuntimeError("x")), "cleanup_unconfirmed", "cleanup"),
        (_Child(returncode=None), "cleanup_unconfirmed", "cleanup"),
    ],
)
async def test_complete_command_settles_untrustworthy_children(
    monkeypatch: pytest.MonkeyPatch, child: _Child, reason: str, phase: str
) -> None:
    _killpg(monkeypatch, _absent)
    with pytest.raises(CommandIncompleteError) as error:
        await proc._complete_command(_process(child), None, False)
    assert _reason(error) == (reason, phase)
    assert child.waited >= 1


async def test_complete_command_refuses_group_that_outlives_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter([True, False])

    def behavior(sig: int) -> None:
        if sig == 0 and not next(states, False):
            raise ProcessLookupError

    _killpg(monkeypatch, behavior)
    child = _Child()
    with pytest.raises(CommandIncompleteError) as error:
        await proc._complete_command(_process(child), None, True)
    assert _reason(error) == ("owned_group_not_empty", "cleanup")


async def test_complete_command_group_permission_failure_is_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _killpg(monkeypatch, _denied)
    with pytest.raises(CommandIncompleteError) as error:
        await proc._complete_command(_process(_Child()), None, True)
    assert _reason(error) == ("cleanup_unconfirmed", "cleanup")


async def test_complete_command_cancellation_settles_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    entered = asyncio.Event()

    def killing(_sig: int) -> None:
        gate.set()
        raise ProcessLookupError

    _killpg(monkeypatch, killing)

    async def hang() -> tuple[bytes | None, bytes | None]:
        entered.set()
        await gate.wait()
        return None, None

    child = _Child(communicate_override=hang)

    task = asyncio.create_task(proc._complete_command(_process(child), None, True))
    try:
        await wait_event_or_task(entered, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.waited == 1
    finally:
        gate.set()
        await _settle_tasks(task)


def _same_directory(printed: str, expected: Path) -> bool:
    return Path(printed.strip()).resolve() == expected.resolve()


async def test_run_command_real_child_uses_cwd_and_owned_group(tmp_path: Path) -> None:
    result = await run_command(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=tmp_path,
        own_process_group=True,
    )
    assert result.returncode == 0
    assert await asyncio.to_thread(_same_directory, result.stdout, tmp_path)


async def test_run_command_real_deadline_is_watchdog_exhaustion() -> None:
    wait_forever = "import threading; threading.Event().wait()"
    with pytest.raises(CommandIncompleteError) as error:
        await run_command(
            [sys.executable, "-c", wait_forever], deadline_seconds=0.2, own_process_group=True
        )
    assert _reason(error) == ("watchdog_exhausted", "execution")


def test_incomplete_error_carries_closed_reason_text() -> None:
    error = CommandIncompleteError("output_capture_failed", "execution")
    assert str(error) == "tool_command_output_capture_failed"
    assert SimpleNamespace(reason=error.reason).reason == "output_capture_failed"
