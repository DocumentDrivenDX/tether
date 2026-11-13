"""Owned real-child controls for selected-profile log cleanup tests."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, cast

import selected_profiles_logs as logs


async def _hold_reader() -> bytes:
    await asyncio.Future()
    return b""


class _ReadGate:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def read(self, size: int) -> bytes:
        chunk = await self.reader.read(size)
        if chunk:
            self.entered.set()
            await self.release.wait()
        return chunk


class _ShieldControl:
    def __init__(self) -> None:
        self.delegate = asyncio
        self.entered = asyncio.Event()
        self.acknowledged: asyncio.Queue[None] = asyncio.Queue()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    async def shield(self, future: asyncio.Future[Any]) -> Any:
        self.entered.set()
        try:
            return await self.delegate.shield(future)
        except asyncio.CancelledError:
            self.acknowledged.put_nowait(None)
            raise


async def _spawn_held_log_follower(
    label: str,
) -> tuple[asyncio.subprocess.Process, logs._Follower, _ReadGate, _ReadGate]:
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            f"import sys; print('stdout {label}', flush=True); "
            f"print('stderr {label}', file=sys.stderr, flush=True); sys.stdin.read()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    process: asyncio.subprocess.Process | None = None
    readers: list[asyncio.Task[bytes]] = []
    claimed_streams: set[int] = set()
    gates: list[_ReadGate] = []
    follower: logs._Follower | None = None
    try:
        process, cancelled_while_spawning = await _acquire_test_child(spawning)
        if cancelled_while_spawning:
            raise asyncio.CancelledError
        if process.stdout is None or process.stderr is None:
            raise AssertionError("fabricated_child_pipe_missing")
        stdout_gate, stderr_gate = _ReadGate(process.stdout), _ReadGate(process.stderr)
        gates.extend((stdout_gate, stderr_gate))
        ready, changed, overflow = asyncio.Event(), asyncio.Event(), asyncio.Event()
        stdout_task = asyncio.create_task(
            logs._read_bounded(
                cast(Any, stdout_gate),
                ready,
                captured=bytearray(),
                changed=changed,
                overflow=overflow,
            )
        )
        readers.append(stdout_task)
        claimed_streams.add(id(process.stdout))
        stderr_task = asyncio.create_task(
            logs._read_bounded(
                cast(Any, stderr_gate),
                asyncio.Event(),
                captured=bytearray(),
                changed=changed,
                overflow=overflow,
            )
        )
        readers.append(stderr_task)
        claimed_streams.add(id(process.stderr))
        follower = logs._Follower(
            process,
            stdout_task,
            stderr_task,
            ready,
            bytearray(),
            bytearray(),
            changed,
            overflow,
            asyncio.create_task(process.wait()),
        )
        await _wait_for_read_gate(stdout_gate, follower)
        await _wait_for_read_gate(stderr_gate, follower)
        return process, follower, stdout_gate, stderr_gate
    except BaseException as primary:
        for gate in gates:
            gate.release.set()
        if process is None and spawning.done() and not spawning.cancelled():
            try:
                process = spawning.result()
            except BaseException:
                process = None
        if process is not None:
            cleanup = asyncio.create_task(
                _cleanup_test_child(process, readers, follower, claimed_streams)
            )
        else:
            cleanup = asyncio.create_task(_settle_tasks((spawning,)))
        try:
            await logs.await_retained(cleanup)
        except BaseException as cleanup_error:
            raise primary from cleanup_error
        raise


async def _acquire_test_child(
    spawning: asyncio.Task[asyncio.subprocess.Process],
) -> tuple[asyncio.subprocess.Process, bool]:
    cancelled = False
    while not spawning.done():
        try:
            await asyncio.shield(spawning)
        except asyncio.CancelledError:
            cancelled = True
    return spawning.result(), cancelled


async def _cleanup_test_child(
    process: asyncio.subprocess.Process,
    readers: list[asyncio.Task[bytes]],
    follower: logs._Follower | None,
    claimed_streams: set[int],
) -> None:
    errors: list[BaseException] = []
    drains = [
        asyncio.create_task(logs._read_bounded(stream, asyncio.Event()))
        for stream in (process.stdout, process.stderr)
        if stream is not None and id(stream) not in claimed_streams
    ]
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
    try:
        await process.wait()
    except BaseException as error:
        errors.append(error)
    outcomes = await asyncio.gather(*readers, *drains, return_exceptions=True)
    errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if follower is not None and follower.process_exit is not None:
        outcomes = await asyncio.gather(follower.process_exit, return_exceptions=True)
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("fabricated_child_cleanup_failed", errors)


async def _wait_for_read_gate(gate: _ReadGate, follower: logs._Follower) -> None:
    entered = asyncio.create_task(gate.entered.wait())
    terminals: tuple[asyncio.Future[Any], ...] = (
        follower.stdout,
        follower.stderr,
        *(() if follower.process_exit is None else (follower.process_exit,)),
    )
    try:
        done, _ = await asyncio.wait((entered, *terminals), return_when=asyncio.FIRST_COMPLETED)
        if entered not in done:
            for task in done:
                if task is not entered:
                    task.result()
            raise AssertionError("fabricated_child_ended_before_reader_gate")
    finally:
        cleanup = asyncio.create_task(_settle_tasks((entered,)))
        await logs.await_retained(cleanup)


async def _settle_tasks(tasks: tuple[asyncio.Future[Any], ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _finish_real_follower(
    process: asyncio.subprocess.Process,
    follower: logs._Follower,
    gates: tuple[_ReadGate, ...],
    operation: asyncio.Task[Any] | None,
) -> None:
    for gate in gates:
        gate.release.set()
    errors: list[BaseException] = []
    if operation is not None:
        outcomes = await asyncio.gather(operation, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, asyncio.CancelledError
            ):
                errors.append(outcome)
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
    try:
        await process.wait()
    except BaseException as error:
        errors.append(error)
    owned: tuple[asyncio.Future[Any], ...] = (
        follower.stdout,
        follower.stderr,
        *(() if follower.process_exit is None else (follower.process_exit,)),
    )
    outcomes = await asyncio.gather(*owned, return_exceptions=True)
    errors.extend(
        outcome
        for outcome in outcomes
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError)
    )
    if errors:
        raise BaseExceptionGroup("fabricated_follower_cleanup_failed", errors)


async def _await_cancel_ack(caller: asyncio.Task[Any], control: _ShieldControl) -> None:
    observed = asyncio.create_task(control.acknowledged.get())
    try:
        done, _ = await asyncio.wait((observed, caller), return_when=asyncio.FIRST_COMPLETED)
        if caller in done:
            caller.result()
            raise AssertionError("follower_cleanup_ended_before_cancel_ack")
        observed.result()
        assert not caller.done()
    finally:
        cleanup = asyncio.create_task(_settle_tasks((observed,)))
        await logs.await_retained(cleanup)
