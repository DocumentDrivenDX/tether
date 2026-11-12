"""Owned direct-child and logical timeout controls for process tests."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
selected_profiles_process = importlib.import_module("selected_profiles_process")
await_retained = selected_profiles_process.await_retained


class OwnedProcess:
    """One direct child with a single protocol reader and continuously drained stderr."""

    def __init__(self, process: Any) -> None:
        self.process = process
        self._stdout = process.stdout
        self._stderr = process.stderr
        self._reader: asyncio.Task[bytes] | None = None
        self._reader_started = asyncio.Event()
        self._read_lock = asyncio.Lock()
        self._stderr_bytes = bytearray()
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._wait_task = asyncio.create_task(process.wait())
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def spawn(cls, *argv: str, **options: Any) -> OwnedProcess:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(*argv, **options))
        try:
            process = await await_retained(spawning)
        except BaseException as primary:
            if not spawning.done():
                try:
                    process = await await_retained(spawning)
                except BaseException as spawn_error:
                    if spawn_error is primary:
                        raise
                    raise BaseExceptionGroup(
                        "process_spawn_and_cancellation_failed", [primary, spawn_error]
                    ) from primary
            else:
                try:
                    process = spawning.result()
                except BaseException:
                    raise primary from None
            owner = cls(process)
            cleanup = asyncio.create_task(owner._close())
            try:
                await await_retained(cleanup)
            except BaseException as cleanup_error:
                if cleanup_error is primary:
                    raise
                raise BaseExceptionGroup(
                    "process_spawn_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            raise
        return cls(process)

    @classmethod
    def from_process(cls, process: Any) -> OwnedProcess:
        """Adopt a process-shaped control double for deterministic owner tests."""
        return cls(process)

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def stderr_text(self) -> str:
        return bytes(self._stderr_bytes).decode("utf-8", errors="replace")

    async def send_json(self, value: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("owned_process_stdin_unavailable")
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        await self.process.stdin.drain()

    async def read_line(self) -> bytes:
        async with self._read_lock:
            if self._stdout is None:
                raise RuntimeError("owned_process_stdout_unavailable")
            reader = asyncio.create_task(self._stdout.readline())
            self._reader = reader
            self._reader_started.set()
            primary: BaseException | None = None
            try:
                done, _ = await asyncio.wait(
                    (reader, self._wait_task), return_when=asyncio.FIRST_COMPLETED
                )
                # A buffered protocol event remains authoritative when normal process exit
                # happens in the same scheduler turn.
                if reader not in done:
                    await asyncio.shield(self._wait_task)
                raw = await reader
            except BaseException as error:
                primary = error
                raise
            finally:
                if not reader.done():
                    reader.cancel()
                join = asyncio.create_task(_join_reader(reader))
                try:
                    await await_retained(join)
                except BaseException as cleanup_error:
                    if primary is not None:
                        raise BaseExceptionGroup(
                            "owned_process_reader_and_cleanup_failed", [primary, cleanup_error]
                        ) from primary
                    raise
                self._reader = None
                self._reader_started.clear()
            return raw

    async def event(self) -> dict[str, Any]:
        raw = await self.read_line()
        if not raw:
            raise RuntimeError("owned_process_protocol_eof: " + self.stderr_text.strip())
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("owned_process_protocol_invalid") from error
        if not isinstance(value, dict):
            raise RuntimeError("owned_process_protocol_invalid")
        return value

    async def command(self, value: str) -> dict[str, Any]:
        await self.send_json({"command": value})
        return await self.event()

    async def wait(self) -> int:
        result = await asyncio.shield(self._wait_task)
        await asyncio.shield(self._stderr_task)
        return result

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await await_retained(self._close_task)

    async def _drain_stderr(self) -> None:
        if self._stderr is None:
            return
        while chunk := await self._stderr.read(4096):
            self._stderr_bytes.extend(chunk)

    async def _close(self) -> None:
        errors: list[BaseException] = []
        reader = self._reader
        if reader is not None and not reader.done():
            reader.cancel()
        if reader is not None:
            result = (await asyncio.gather(reader, return_exceptions=True))[0]
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                errors.append(result)

        try:
            if self.process.returncode is None:
                self.process.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)

        stdout_drain = (
            asyncio.create_task(self._stdout.read()) if self._stdout is not None else None
        )
        tasks: tuple[asyncio.Task[Any], ...] = tuple(
            task for task in (stdout_drain, self._wait_task, self._stderr_task) if task is not None
        )
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
        if errors:
            raise BaseExceptionGroup("owned_process_cleanup_failed", errors)


class ShieldCancellationObserver:
    """Observe shield cancellation for one task without patching shared asyncio."""

    def __init__(self, acknowledgements: int = 2) -> None:
        self.target: asyncio.Task[Any] | None = None
        self.additional_targets: set[asyncio.Task[Any]] = set()
        self.entered = asyncio.Event()
        self.acknowledged = tuple(asyncio.Event() for _ in range(acknowledgements))
        self.shield_entries = tuple(asyncio.Event() for _ in range(acknowledgements + 1))
        self.seen = 0
        self.entries = 0

    def install(self, module: ModuleType, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            module,
            "asyncio",
            _DelegatingShieldAsyncio(cast(Any, module).asyncio, self),
        )


class _DelegatingShieldAsyncio:
    def __init__(self, real_asyncio: Any, observer: ShieldCancellationObserver) -> None:
        self._asyncio = real_asyncio
        self._observer = observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._asyncio, name)

    async def shield(self, future: asyncio.Future[Any]) -> Any:
        current = self._asyncio.current_task()
        tracked = current is not None and (
            current is self._observer.target or current in self._observer.additional_targets
        )
        if tracked:
            self._observer.entered.set()
            self._observer.entries += 1
            if self._observer.entries <= len(self._observer.shield_entries):
                self._asyncio.get_running_loop().call_soon(
                    self._observer.shield_entries[self._observer.entries - 1].set
                )
        try:
            return await self._asyncio.shield(future)
        except self._asyncio.CancelledError:
            tracked = current is not None and (
                current is self._observer.target or current in self._observer.additional_targets
            )
            if tracked and current is not None and current.cancelling():
                self._observer.seen += 1
                if self._observer.seen <= len(self._observer.acknowledged):
                    self._asyncio.get_running_loop().call_soon(
                        self._observer.acknowledged[self._observer.seen - 1].set
                    )
            raise


def owner_for_process(process: Any) -> OwnedProcess:
    """Reuse one reader/stderr/reap owner for legacy raw-process consumers."""
    if isinstance(process, OwnedProcess):
        return process
    owner = getattr(process, "_tether_test_owner", None)
    if owner is None:
        owner = OwnedProcess.from_process(process)
        process._tether_test_owner = owner
    return owner


async def receive(process: Any) -> dict[str, Any]:
    return await owner_for_process(process).event()


async def stop(process: Any) -> None:
    await owner_for_process(process).close()


async def _join_reader(reader: asyncio.Task[bytes]) -> None:
    await asyncio.gather(reader, return_exceptions=True)


class RetainedCleanup:
    """Register acquired test resources and settle every closer in one retained task."""

    def __init__(self) -> None:
        self._closers: list[tuple[Callable[[], Any], ...]] = []

    def add(self, *steps: Callable[[], Any]) -> None:
        if not steps:
            raise ValueError("owned_test_cleanup_steps_required")
        self._closers.insert(0, steps)

    async def __aenter__(self) -> RetainedCleanup:
        return self

    async def __aexit__(self, exc_type: Any, primary: BaseException | None, _tb: Any) -> bool:
        cleanup = asyncio.create_task(self._close_all())
        try:
            await await_retained(cleanup)
        except BaseException as cleanup_error:
            if primary is not None and cleanup_error is primary:
                return False
            if primary is not None:
                raise BaseExceptionGroup(
                    "owned_test_resource_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            raise
        return False

    async def _close_all(self) -> None:
        errors: list[BaseException] = []
        for steps in self._closers:
            for index, step in enumerate(steps):
                try:
                    result = await step()
                    if result is False and index == len(steps) - 1:
                        errors.append(AssertionError("owned_test_resource_drain_incomplete"))
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise BaseExceptionGroup("owned_test_resources_cleanup_failed", errors)


class _DelegatingTimeouts:
    def __init__(self, captured: list[float | None]) -> None:
        self.captured = captured

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    def timeout(self, seconds: float | None = None) -> asyncio.Timeout:
        self.captured.append(seconds)
        return asyncio.timeout(None)


@contextmanager
def unarmed_timeouts(*modules: ModuleType) -> Iterator[list[float | None]]:
    """Capture selected module calls while leaving shared asyncio and its loop untouched."""
    captured: list[float | None] = []
    facade = _DelegatingTimeouts(captured)
    originals = [cast(Any, module).asyncio for module in modules]
    try:
        for module in modules:
            cast(Any, module).asyncio = facade
        yield captured
    finally:
        for module, original in zip(modules, originals, strict=True):
            cast(Any, module).asyncio = original


@contextmanager
def model_execution_timeouts() -> Iterator[list[float | None]]:
    modules = tuple(
        importlib.import_module(name)
        for name in (
            "tether.runtime.pydantic_ai.bridge",
            "tether.runtime.pydantic_ai.response",
            "tether.engine.tools",
        )
    )
    with unarmed_timeouts(*modules) as captured:
        yield captured


@contextmanager
def summary_execution_controls(
    context_config: Any,
) -> Iterator[tuple[list[float | None], list[float]]]:
    context_step = importlib.import_module("tether.engine.context.step")
    with (
        model_execution_timeouts() as model_timeouts,
        frozen_context_clock(context_step, context_config.timeout_seconds) as deadlines,
    ):
        yield model_timeouts, deadlines


class _FrozenLoop:
    def __init__(self, loop: asyncio.AbstractEventLoop, logical_time: float) -> None:
        self._loop, self._logical_time = loop, logical_time

    def time(self) -> float:
        return self._logical_time

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loop, name)


class _ContextClock:
    def __init__(self, deadlines: list[float], logical_time: float) -> None:
        self.deadlines, self.logical_time = deadlines, logical_time

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    def get_running_loop(self) -> _FrozenLoop:
        return _FrozenLoop(asyncio.get_running_loop(), self.logical_time)

    def timeout_at(self, deadline: float) -> asyncio.Timeout:
        self.deadlines.append(deadline)
        return asyncio.timeout(None)


@contextmanager
def frozen_context_clock(
    context_step_module: ModuleType, timeout_seconds: float, *, logical_time: float = 1000.0
) -> Iterator[list[float]]:
    """Freeze only ContextStep's clock and leave its real timeout unarmed."""
    deadlines: list[float] = []
    previous = cast(Any, context_step_module).asyncio
    try:
        cast(Any, context_step_module).asyncio = _ContextClock(deadlines, logical_time)
        yield deadlines
    finally:
        cast(Any, context_step_module).asyncio = previous
    assert all(deadline - logical_time == timeout_seconds for deadline in deadlines)


async def exercise_process_owner_controls() -> None:
    await _exercise_early_process_exit()
    await _exercise_repeated_cleanup_cancellation()


async def _exercise_early_process_exit() -> None:
    child = await OwnedProcess.spawn(
        sys.executable,
        "-c",
        "import sys; print('startup-failure-witness', file=sys.stderr)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            await child.event()
        except RuntimeError as error:
            refusal = str(error)
        else:
            raise AssertionError("early_child_exit_must_refuse_missing_event")
        assert "owned_process_protocol_eof" in refusal
        await child.wait()
    finally:
        await child.close()
    assert child.returncode == 0
    assert "startup-failure-witness" in child.stderr_text


async def _exercise_repeated_cleanup_cancellation() -> None:
    owner = await OwnedProcess.spawn(
        sys.executable,
        "-c",
        "import asyncio, json; print(json.dumps({'event': 'ready'}), flush=True); "
        "asyncio.run(asyncio.Event().wait())",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    gate_entered, gate_release = asyncio.Event(), asyncio.Event()
    owner._stdout = _PostEofGate(owner._stdout, gate_entered, gate_release)
    acknowledgements: asyncio.Queue[None] = asyncio.Queue()
    original_asyncio = cast(Any, selected_profiles_process).asyncio
    shield_facade = _ShieldAcknowledgements(original_asyncio, acknowledgements)
    cast(Any, selected_profiles_process).asyncio = shield_facade
    observers: list[asyncio.Task[Any]] = []
    close_caller: asyncio.Task[None] | None = None
    primary: BaseException | None = None
    try:
        assert (await owner.event())["event"] == "ready"
        await _cancel_native_observers(owner, observers)
        close_caller = await _cancel_close_repeatedly(
            owner, gate_entered, gate_release, acknowledgements, observers
        )
    except BaseException as error:
        primary = error
        raise
    finally:
        finalizer = asyncio.create_task(
            _finalize_native_owner(owner, gate_release, observers, close_caller)
        )
        try:
            await await_retained(finalizer)
        except BaseException as cleanup_error:
            if primary is not None and cleanup_error is primary:
                pass
            elif primary is not None:
                raise BaseExceptionGroup(
                    "native_process_control_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            else:
                raise
        finally:
            cast(Any, selected_profiles_process).asyncio = original_asyncio


async def _cancel_native_observers(owner: OwnedProcess, observers: list[asyncio.Task[Any]]) -> None:
    wait_started = asyncio.Event()
    wait_caller = asyncio.create_task(_observe_wait(owner, wait_started))
    observers.append(wait_caller)
    await wait_started.wait()
    wait_caller.cancel()
    wait_result = (await asyncio.gather(wait_caller, return_exceptions=True))[0]
    assert isinstance(wait_result, asyncio.CancelledError)
    assert not owner._wait_task.done()
    assert not owner._wait_task.cancelled()
    assert not owner._stderr_task.done()
    assert not owner._stderr_task.cancelled()

    read_caller = asyncio.create_task(owner.event())
    observers.append(read_caller)
    await owner._reader_started.wait()
    reader = owner._reader
    assert reader is not None
    read_caller.cancel()
    read_result = (await asyncio.gather(read_caller, return_exceptions=True))[0]
    assert isinstance(read_result, asyncio.CancelledError)
    assert reader.done()
    assert owner._reader is None


async def _cancel_close_repeatedly(
    owner: OwnedProcess,
    gate_entered: asyncio.Event,
    gate_release: asyncio.Event,
    acknowledgements: asyncio.Queue[None],
    observers: list[asyncio.Task[Any]],
) -> asyncio.Task[None]:
    caller = asyncio.create_task(owner.close())
    observers.append(caller)
    await _wait_for_gate_or_caller(gate_entered, caller, observers)
    await asyncio.gather(asyncio.shield(owner._wait_task), asyncio.shield(owner._stderr_task))
    assert owner.returncode is not None
    assert owner.returncode < 0
    assert owner._wait_task.done()
    assert not owner._wait_task.cancelled()
    assert owner._stderr_task.done()
    assert not owner._stderr_task.cancelled()
    for _ in range(2):
        caller.cancel()
        await _ack_or_owner_end(acknowledgements, caller)
        assert not caller.done()
        assert owner._close_task is not None
        assert not owner._close_task.done()
    assert not gate_release.is_set()
    gate_release.set()
    try:
        await await_retained(caller)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("caller_cancellation_must_be_preserved")
    assert owner._close_task is not None
    assert owner._close_task.done()
    assert owner.returncode is not None
    assert owner.returncode < 0
    return caller


class _PostEofGate:
    def __init__(self, reader: Any, entered: asyncio.Event, release: asyncio.Event) -> None:
        self._reader, self._entered, self._release = reader, entered, release

    async def readline(self) -> bytes:
        return await self._reader.readline()

    async def read(self, size: int = -1) -> bytes:
        value = await self._reader.read(size)
        if not value:
            self._entered.set()
            await self._release.wait()
        return value


async def _observe_wait(owner: OwnedProcess, started: asyncio.Event) -> int:
    started.set()
    return await owner.wait()


async def _wait_for_gate_or_caller(
    gate_entered: asyncio.Event,
    caller: asyncio.Task[None],
    observers: list[asyncio.Task[Any]],
) -> None:
    gate_waiter = asyncio.create_task(gate_entered.wait())
    observers.append(gate_waiter)
    done, _ = await asyncio.wait((gate_waiter, caller), return_when=asyncio.FIRST_COMPLETED)
    if caller in done and gate_waiter not in done:
        await caller
        raise AssertionError("native_cleanup_completed_before_eof_gate")
    assert gate_waiter.result()


async def _finalize_native_owner(
    owner: OwnedProcess,
    gate_release: asyncio.Event,
    observers: list[asyncio.Task[Any]],
    close_caller: asyncio.Task[None] | None,
) -> None:
    gate_release.set()
    cleanup_caller = asyncio.create_task(owner.close())
    for observer in observers:
        if not observer.done():
            observer.cancel()
    tasks: tuple[asyncio.Task[Any], ...] = tuple(
        task for task in (*observers, close_caller, cleanup_caller) if task is not None
    )
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [
        error
        for task, error in zip(tasks, outcomes, strict=True)
        if task is cleanup_caller
        and isinstance(error, BaseException)
        and not isinstance(error, asyncio.CancelledError)
    ]
    if errors:
        raise BaseExceptionGroup("native_process_cleanup_failed", errors)


class _ShieldAcknowledgements:
    def __init__(self, delegate: Any, acknowledgements: asyncio.Queue[None]) -> None:
        self._delegate, self._acknowledgements = delegate, acknowledgements

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def shield(self, task: asyncio.Future[Any]) -> Any:
        try:
            return await self._delegate.shield(task)
        except asyncio.CancelledError:
            self._acknowledgements.put_nowait(None)
            raise


async def _ack_or_owner_end(
    acknowledgements: asyncio.Queue[None], caller: asyncio.Task[None]
) -> None:
    ack = asyncio.create_task(acknowledgements.get())
    try:
        done, _ = await asyncio.wait((ack, caller), return_when=asyncio.FIRST_COMPLETED)
        if caller in done and ack not in done:
            await caller
            raise AssertionError("cleanup_caller_ended_before_cancel_ack")
        assert ack.result() is None
    finally:
        if not ack.done():
            ack.cancel()
        await asyncio.gather(ack, return_exceptions=True)
