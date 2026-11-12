"""Narrow lifetime owners for embedded PostgreSQL tests."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from postgres_pending_io_controls import PendingIOControl
from process_lifetime_controls import OwnedProcess, await_retained, selected_profiles_process

from tether.stores.postgres.embedded import EmbeddedPostgres
from tether.stores.postgres.embedded.files import binaries
from tether.stores.postgres.embedded.server import _LIVE

_UNSETTLED_ORPHANS: set[str] = set()


def arm_captured_timeout(control: PendingIOControl, seconds: float) -> asyncio.Timeout:
    captured = next(
        (wait for wait in control.waits if not wait.armed and wait.seconds == seconds), None
    )
    if captured is None:
        raise AssertionError(f"embedded_timeout_not_captured: {control.durations!r}")
    captured.armed = True
    captured.timer.reschedule(asyncio.get_running_loop().time() + seconds)
    return captured.timer


async def close_embedded(server: EmbeddedPostgres, primary: BaseException | None = None) -> bool:
    cleanup = asyncio.create_task(_close_embedded(server))
    try:
        return await await_retained(cleanup)
    except BaseException as error:
        if primary is None:
            raise
        if error is primary:
            raise
        raise BaseExceptionGroup(
            "embedded_operation_and_close_failed", [primary, error]
        ) from primary


async def _close_embedded(server: EmbeddedPostgres) -> bool:
    physical, failures = _begin_embedded_close(server)
    bounded = await _settle_embedded_close(server, physical, failures)
    if failures:
        raise BaseExceptionGroup("embedded_server_cleanup_failed", failures)
    return bounded


def _begin_embedded_close(
    server: EmbeddedPostgres,
) -> tuple[asyncio.Task[None] | None, list[BaseException]]:
    failures: list[BaseException] = []
    try:
        server._seal()
    except BaseException as error:
        failures.append(error)
    physical: asyncio.Task[None] | None = None
    try:
        physical = server._ensure_close()
    except BaseException as error:
        failures.append(error)
    return physical, failures


async def _settle_embedded_close(
    server: EmbeddedPostgres,
    physical: asyncio.Task[None] | None,
    failures: list[BaseException],
) -> bool:
    bounded = False
    try:
        bounded = await server.aclose()
    except BaseException as error:
        _append_unique(failures, error)
    if physical is not None:
        try:
            await await_retained(physical)
        except BaseException as error:
            _append_unique(failures, error)
    failures.extend(_physical_errors(server))
    return bounded


def _append_unique(errors: list[BaseException], error: BaseException) -> None:
    if not any(error is previous for previous in errors):
        errors.append(error)


@asynccontextmanager
async def owned_server(config: Any):
    server = await EmbeddedPostgres.start(config)
    primary: BaseException | None = None
    try:
        yield server
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = asyncio.create_task(close_embedded(server))
        try:
            await await_retained(cleanup)
        except BaseException as error:
            if primary is None:
                raise
            raise BaseExceptionGroup(
                "embedded_test_and_server_close_failed", [primary, error]
            ) from primary


async def close_matching(data_dir: Path, primary: BaseException | None = None) -> None:
    async def finish_all() -> None:
        servers = await asyncio.to_thread(_matching_servers, data_dir, tuple(_LIVE))
        outcomes = await asyncio.gather(
            *(close_embedded(server) for server in servers), return_exceptions=True
        )
        errors = [value for value in outcomes if isinstance(value, BaseException)]
        if errors:
            raise BaseExceptionGroup("embedded_matching_servers_cleanup_failed", errors)

    cleanup = asyncio.create_task(finish_all())
    try:
        await await_retained(cleanup)
    except BaseException as error:
        if primary is None:
            raise
        raise BaseExceptionGroup(
            "embedded_test_and_owner_cleanup_failed", [primary, error]
        ) from primary


async def settle_embedded_server(
    server: EmbeddedPostgres,
    release: Callable[[], None],
    waiters: tuple[asyncio.Future[Any], ...] = (),
    expected_cancellation: tuple[asyncio.Future[Any], ...] = (),
    primary: BaseException | None = None,
) -> None:
    async def finish() -> None:
        errors: list[BaseException] = []
        try:
            release()
        except BaseException as error:
            errors.append(error)
        close_task = asyncio.create_task(close_embedded(server))

        async def join_waiters() -> None:
            outcomes = await asyncio.gather(*waiters, return_exceptions=True)
            errors = [
                outcome
                for owner, outcome in zip(waiters, outcomes, strict=True)
                if isinstance(outcome, BaseException)
                and not (
                    isinstance(outcome, asyncio.CancelledError) and owner in expected_cancellation
                )
            ]
            if errors:
                raise BaseExceptionGroup("embedded_waiter_cleanup_failed", errors)

        wait_task = asyncio.create_task(join_waiters())
        results = await asyncio.gather(close_task, wait_task, return_exceptions=True)
        errors.extend(result for result in results if isinstance(result, BaseException))
        if errors:
            raise BaseExceptionGroup("embedded_test_owners_cleanup_failed", errors)

    cleanup = asyncio.create_task(finish())
    try:
        await await_retained(cleanup)
    except BaseException as error:
        if primary is None:
            raise
        raise BaseExceptionGroup("embedded_test_and_cleanup_failed", [primary, error]) from primary


def _physical_errors(server: EmbeddedPostgres) -> list[BaseException]:
    errors: list[BaseException] = []
    if server in _LIVE:
        errors.append(AssertionError("embedded_server_still_registered"))
    files = server._files
    if files is not None and files.lock_fd is not None:
        errors.append(AssertionError("embedded_directory_lock_still_owned"))
    for name in ("initialization", "probe", "server"):
        process = getattr(server._processes, name)
        if process is not None and process.returncode is None:
            errors.append(AssertionError(f"embedded_{name}_process_still_running"))
    return errors


def runtime_cleanup_safe(data_dir: Path) -> bool:
    return os.path.realpath(data_dir) not in _UNSETTLED_ORPHANS


def _matching_servers(data_dir: Path, servers: tuple[EmbeddedPostgres, ...]):
    target = os.path.realpath(data_dir)
    return tuple(server for server in servers if os.path.realpath(server.config.data_dir) == target)


@dataclass(frozen=True)
class NativeIdentity:
    pid: int
    creation_time: float
    executable: Path
    arguments: tuple[str, ...]
    data_directory: Path


class RetainedCancellationControl:
    """Observe actual shield cancellation and the next retainer iteration."""

    def __init__(self) -> None:
        self.entered: asyncio.Queue[None] = asyncio.Queue()
        self.acknowledged: asyncio.Queue[None] = asyncio.Queue()
        self.original: Any = None

    def install(self, monkeypatch: Any) -> None:
        self.original = selected_profiles_process.asyncio
        monkeypatch.setattr(selected_profiles_process, "asyncio", _ShieldControl(self))


async def cancel_retained_waiter_twice(
    waiter: asyncio.Task[Any], owner: asyncio.Task[Any], control: RetainedCancellationControl
) -> None:
    await _queue_or_owner_end(control.entered, waiter, owner)
    for _ in range(2):
        waiter.cancel()
        await _queue_or_owner_end(control.acknowledged, waiter, owner)
        assert not waiter.done()
        assert not owner.done()
        await _queue_or_owner_end(control.entered, waiter, owner)


async def _queue_or_owner_end(
    queue: asyncio.Queue[None], waiter: asyncio.Task[Any], owner: asyncio.Task[Any]
) -> None:
    observed = asyncio.create_task(queue.get())
    try:
        done, _ = await asyncio.wait((observed, waiter, owner), return_when=asyncio.FIRST_COMPLETED)
        if observed in done:
            observed.result()
            return
        for task in (waiter, owner):
            if task in done:
                if task.cancelled():
                    raise AssertionError("retained_cleanup_owner_cancelled_before_ack")
                task.result()
        raise AssertionError("retained_cleanup_owner_ended_before_ack")
    finally:
        if not observed.done():
            observed.cancel()
        await asyncio.gather(observed, return_exceptions=True)


class _ShieldControl:
    def __init__(self, owner: RetainedCancellationControl) -> None:
        self.owner = owner
        self.delegate = owner.original

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    async def shield(self, future: asyncio.Future[Any]) -> Any:
        self.owner.entered.put_nowait(None)
        try:
            return await self.delegate.shield(future)
        except asyncio.CancelledError:
            self.owner.acknowledged.put_nowait(None)
            raise


def capture_native_identity(
    pid: int, data_directory: Path, binary_directory: Path
) -> NativeIdentity:
    data = data_directory.resolve(strict=True)
    pidfile = _read_pidfile(data / "postmaster.pid")
    if pidfile != (pid, data):
        raise RuntimeError("embedded_native_pidfile_identity_mismatch")
    process = psutil.Process(pid)
    try:
        creation_time = process.create_time()
        executable = Path(process.exe()).resolve(strict=True)
        arguments = tuple(process.cmdline())
    except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
        raise RuntimeError("embedded_native_identity_unavailable") from error
    expected = (binary_directory / "postgres").resolve(strict=True)
    if executable != expected or not _argv_data_directory(arguments, data):
        raise RuntimeError("embedded_native_executable_or_directory_mismatch")
    return NativeIdentity(pid, creation_time, executable, arguments, data)


def verify_native_identity(identity: NativeIdentity) -> psutil.Process:
    if _read_pidfile(identity.data_directory / "postmaster.pid") != (
        identity.pid,
        identity.data_directory,
    ):
        raise RuntimeError("embedded_native_pidfile_identity_mismatch")
    process = psutil.Process(identity.pid)
    try:
        observed = (
            process.create_time(),
            Path(process.exe()).resolve(strict=True),
            tuple(process.cmdline()),
        )
    except (psutil.AccessDenied, psutil.NoSuchProcess) as error:
        raise RuntimeError("embedded_native_identity_unavailable") from error
    expected = (identity.creation_time, identity.executable, identity.arguments)
    if observed != expected:
        raise RuntimeError("embedded_native_identity_changed")
    return process


def assert_native_absent(identity: NativeIdentity) -> None:
    try:
        process = psutil.Process(identity.pid)
        creation_time = process.create_time()
    except psutil.NoSuchProcess:
        creation_time = None
    except psutil.AccessDenied as error:
        raise RuntimeError("embedded_native_absence_unknown") from error
    if creation_time == identity.creation_time:
        raise AssertionError("embedded_original_native_process_still_running")
    if (identity.data_directory / "postmaster.pid").exists():
        raise AssertionError("embedded_postmaster_pidfile_still_exists")


async def stop_captured_native(identity: NativeIdentity, binary_directory: Path) -> None:
    cleanup = asyncio.create_task(_stop_captured_native(identity, binary_directory))
    await await_retained(cleanup)


async def _stop_captured_native(identity: NativeIdentity, binary_directory: Path) -> None:
    process = verify_native_identity(identity)
    utility = await OwnedProcess.spawn(
        str(binary_directory / "pg_ctl"),
        "-D",
        str(identity.data_directory),
        "-m",
        "fast",
        "-W",
        "stop",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": str(binary_directory), "LC_ALL": "C", "TZ": "UTC"},
        start_new_session=True,
    )
    errors: list[BaseException] = []
    try:
        try:
            status = await utility.wait()
            if status != 0:
                errors.append(RuntimeError("embedded_pg_ctl_stop_failed: " + utility.stderr_text))
        except BaseException as error:
            errors.append(error)
    finally:
        try:
            await utility.close()
        except BaseException as error:
            errors.append(error)
    if utility.returncode == 0:
        native_wait = asyncio.create_task(asyncio.to_thread(process.wait))
        try:
            await await_retained(native_wait)
            assert_native_absent(identity)
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("embedded_native_stop_and_drain_failed", errors)


class OrphanedServer:
    def __init__(self, identity: NativeIdentity, binary_directory: Path) -> None:
        self.identity = identity
        self.binary_directory = binary_directory
        self.stop_attempted = False

    async def stop(self) -> None:
        if self.stop_attempted:
            raise RuntimeError("embedded_native_stop_already_attempted")
        self.stop_attempted = True

        async def finish() -> None:
            verify_native_identity(self.identity)
            await stop_captured_native(self.identity, self.binary_directory)
            _UNSETTLED_ORPHANS.discard(str(self.identity.data_directory))

        cleanup = asyncio.create_task(finish())
        try:
            await await_retained(cleanup)
        except asyncio.CancelledError:
            if cleanup.done() and not cleanup.cancelled():
                cleanup.result()
            raise
        except BaseException:
            _UNSETTLED_ORPHANS.add(str(self.identity.data_directory))
            raise


@asynccontextmanager
async def orphaned_server(config: Any):
    acquisition = asyncio.create_task(_create_orphan(config))
    try:
        owner = await await_retained(acquisition)
    except asyncio.CancelledError as primary:
        if acquisition.done() and not acquisition.cancelled():
            try:
                owner = acquisition.result()
            except BaseException:
                raise primary from None
            cleanup = asyncio.create_task(owner.stop())
            try:
                await await_retained(cleanup)
            except BaseException as error:
                raise BaseExceptionGroup(
                    "embedded_orphan_cancellation_and_cleanup_failed", [primary, error]
                ) from primary
        raise
    primary: BaseException | None = None
    try:
        yield owner
    except BaseException as error:
        primary = error
        raise
    finally:
        if not owner.stop_attempted:
            try:
                await owner.stop()
            except BaseException as error:
                if primary is None:
                    raise
                raise BaseExceptionGroup(
                    "embedded_orphan_test_and_stop_failed", [primary, error]
                ) from primary


async def _create_orphan(config: Any) -> OrphanedServer:
    data_key = await asyncio.to_thread(os.path.realpath, config.data_dir)
    _UNSETTLED_ORPHANS.add(data_key)
    binary_directory = binaries()
    peer = await OwnedProcess.spawn(
        sys.executable,
        str(Path(__file__).with_name("embedded_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    identity: NativeIdentity | None = None
    try:
        if peer.process.stdin is None:
            raise RuntimeError("embedded_wrapper_stdin_unavailable")
        peer.process.stdin.write(config.model_dump_json().encode() + b"\n")
        await peer.process.stdin.drain()
        raw = await peer.read_line()
        if not raw:
            raise RuntimeError("embedded_wrapper_exited_before_native_pid: " + peer.stderr_text)
        identity = capture_native_identity(int(raw.strip()), config.data_dir, binary_directory)
        verify_native_identity(identity)
        peer.process.kill()
        await peer.wait()
        await peer.close()
        return OrphanedServer(identity, binary_directory)
    except BaseException as primary:
        try:
            await _settle_orphan_acquisition(peer, identity, binary_directory)
        except BaseException as error:
            if identity is not None:
                _UNSETTLED_ORPHANS.add(str(identity.data_directory))
            raise BaseExceptionGroup(
                "embedded_orphan_acquisition_failed", [primary, error]
            ) from primary
        raise


async def _settle_orphan_acquisition(
    peer: OwnedProcess, identity: NativeIdentity | None, binary_directory: Path
) -> None:
    errors: list[BaseException] = []
    await _settle_wrapper_process(peer, identity, errors)
    await _settle_native_identity(identity, binary_directory, errors)
    if errors:
        raise BaseExceptionGroup("embedded_orphan_acquisition_cleanup_failed", errors)


async def _settle_wrapper_process(
    peer: OwnedProcess, identity: NativeIdentity | None, errors: list[BaseException]
) -> None:
    if peer.returncode is None:
        if identity is None:
            try:
                if peer.process.stdin is not None:
                    peer.process.stdin.close()
                    await peer.process.stdin.wait_closed()
            except BaseException as error:
                errors.append(error)
        else:
            try:
                peer.process.kill()
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)
    try:
        await peer.wait()
    except BaseException as error:
        errors.append(error)
    try:
        await peer.close()
    except BaseException as error:
        errors.append(error)


async def _settle_native_identity(
    identity: NativeIdentity | None,
    binary_directory: Path,
    errors: list[BaseException],
) -> None:
    if identity is None:
        return
    try:
        verify_native_identity(identity)
        await stop_captured_native(identity, binary_directory)
    except BaseException as error:
        errors.append(error)
    else:
        if not errors:
            _UNSETTLED_ORPHANS.discard(str(identity.data_directory))


def _read_pidfile(path: Path) -> tuple[int, Path]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        pid, data = int(lines[0]), Path(lines[1]).resolve(strict=True)
    except (OSError, UnicodeDecodeError, IndexError, ValueError) as error:
        raise RuntimeError("embedded_native_pidfile_unavailable") from error
    return pid, data


def _argv_data_directory(arguments: tuple[str, ...], data: Path) -> bool:
    try:
        index = arguments.index("-D")
        return Path(arguments[index + 1]).resolve(strict=True) == data
    except (ValueError, IndexError, OSError):
        return False


__all__ = [
    "NativeIdentity",
    "OrphanedServer",
    "RetainedCancellationControl",
    "arm_captured_timeout",
    "assert_native_absent",
    "cancel_retained_waiter_twice",
    "capture_native_identity",
    "close_embedded",
    "close_matching",
    "orphaned_server",
    "owned_server",
    "runtime_cleanup_safe",
    "settle_embedded_server",
    "stop_captured_native",
]
