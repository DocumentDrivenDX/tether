"""A remote-only installed client calls a separate installed ASGI server over TCP."""

from __future__ import annotations

import asyncio
import secrets
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

import httpx
from process import CommandIncompleteError
from web_embedder import Check
from web_remote_capture import (
    StderrDrain,
    drain_stderr,
)
from web_remote_capture import (
    stop_server as _stop_server,
)
from web_remote_programs import REMOTE, SERVER

from tether.client.remote import RemoteClient, RemoteClientSettings
from tether.client.remote.errors import RemoteTransportError
from tether.client.remote.wire import problem

T = TypeVar("T")


def python_in(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


@dataclass(frozen=True)
class ObservationPolicy:
    """Optional caller-owned observation stop; unset means observe until state changes."""

    stop: asyncio.Event | None = None


@dataclass(frozen=True)
class ProbeAttempt:
    client: RemoteClient | None = None
    retry_transport: bool = False
    failure: Exception | None = None


def _terminal_reason(
    done: set[asyncio.Future[object]],
    *,
    process_exit: asyncio.Task[int],
    stderr_task: asyncio.Task[StderrDrain],
    overflow_waiter: asyncio.Task[bool],
    stop_waiter: asyncio.Task[bool] | None,
    stage: str,
) -> Exception | None:
    reason = None
    if stop_waiter is not None and stop_waiter in done:
        reason = CommandIncompleteError("watchdog_exhausted", "execution")
    elif overflow_waiter in done:
        reason = CommandIncompleteError("output_capture_failed", "execution")
    elif stderr_task in done:
        if stderr_task.cancelled():
            reason = CommandIncompleteError("output_capture_failed", "execution")
        else:
            try:
                result = stderr_task.result()
            except BaseException:
                reason = CommandIncompleteError("output_capture_failed", "execution")
            else:
                reason = (
                    CommandIncompleteError("output_capture_failed", "execution")
                    if result.overflowed or result.read_failed
                    else ValueError(f"installed_api_server_stderr_closed_{stage}")
                )
    elif process_exit in done:
        reason = ValueError(f"installed_api_server_exited_{stage}")
    return reason


def _ready_state_failure(
    process: asyncio.subprocess.Process,
    process_exit: asyncio.Task[int],
    stderr_task: asyncio.Task[StderrDrain],
    overflow_waiter: asyncio.Task[bool],
    stop_waiter: asyncio.Task[bool] | None,
) -> Exception | None:
    if process.returncode is not None:
        return ValueError("installed_api_server_exited_before_readiness")
    watched: set[asyncio.Future[object]] = {
        cast(asyncio.Future[object], process_exit),
        cast(asyncio.Future[object], stderr_task),
        cast(asyncio.Future[object], overflow_waiter),
    }
    if stop_waiter is not None:
        watched.add(cast(asyncio.Future[object], stop_waiter))
    return _terminal_reason(
        {task for task in watched if task.done()},
        process_exit=process_exit,
        stderr_task=stderr_task,
        overflow_waiter=overflow_waiter,
        stop_waiter=stop_waiter,
        stage="before_readiness",
    )


async def _retained(task: asyncio.Task[T]) -> tuple[T, bool]:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def _cancel_tasks(tasks: list[asyncio.Task[object]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _spawn_server(
    directory: Path, port: int, challenge: str, instance_nonce: str
) -> asyncio.subprocess.Process:
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            str(python_in(directory / "combined")),
            "-I",
            "-c",
            SERVER,
            str(port),
            challenge,
            instance_nonce,
            cwd=directory,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            server, _ = await _retained(spawn)
        except Exception:
            raise CommandIncompleteError("dispatch_failed", "spawn") from None
        if server.stderr is None:
            await _retained(asyncio.create_task(_kill_and_reap(server)))
            raise CommandIncompleteError("cleanup_unconfirmed", "spawn") from None
        overflow = asyncio.Event()
        drain = asyncio.create_task(drain_stderr(server.stderr, overflow))
        _, cancelled = await _retained(asyncio.create_task(_stop_server(server, drain, overflow)))
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    except Exception:
        raise CommandIncompleteError("dispatch_failed", "spawn") from None


async def _close_client(client: RemoteClient | None) -> None:
    if client is not None:
        await client.aclose()


async def _close_client_retained(client: RemoteClient | None) -> bool:
    _, cancelled = await _retained(asyncio.create_task(_close_client(client)))
    return cancelled


async def _kill_and_reap(server: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        server.kill()
    await server.wait()


async def _probe_api(base_url: str, challenge: str, instance_nonce: str) -> RemoteClient:
    class Credential:
        async def authorization(self) -> str:
            return "Bearer " + challenge

    async def credential() -> Credential:
        return Credential()

    client = await RemoteClient.open(
        RemoteClientSettings(base_url=base_url, credential_provider=credential)
    )
    try:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=30) as witness:
            response = await witness.get(
                "/api/v1/capabilities", headers={"Authorization": "Bearer " + challenge}
            )
        if response.is_error:
            raise problem(response)
        if (
            response.status_code != 200
            or response.headers.get("x-tether-instance") != instance_nonce
        ):
            raise ValueError("installed_api_instance_mismatch")
    except httpx.TransportError:
        cancelled = await _close_client_retained(client)
        if cancelled:
            raise asyncio.CancelledError from None
        raise RemoteTransportError("remote_transport_unavailable") from None
    except BaseException:
        cancelled = await _close_client_retained(client)
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    return client


async def _cancel_probe(probe: asyncio.Task[RemoteClient]) -> None:
    async def settle() -> None:
        if not probe.done():
            probe.cancel()
        result = await asyncio.gather(probe, return_exceptions=True)
        client = result[0]
        if isinstance(client, RemoteClient):
            await client.aclose()

    _, cancelled = await _retained(asyncio.create_task(settle()))
    if cancelled:
        raise asyncio.CancelledError


async def _readiness_attempt(
    probe: asyncio.Task[RemoteClient],
    process_exit: asyncio.Task[int],
    stderr_task: asyncio.Task[StderrDrain],
    overflow_waiter: asyncio.Task[bool],
    stop_waiter: asyncio.Task[bool] | None,
) -> ProbeAttempt:
    watched: set[asyncio.Future[object]] = {
        cast(asyncio.Future[object], probe),
        cast(asyncio.Future[object], process_exit),
        cast(asyncio.Future[object], stderr_task),
        cast(asyncio.Future[object], overflow_waiter),
    }
    if stop_waiter is not None:
        watched.add(cast(asyncio.Future[object], stop_waiter))
    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
    terminal = _terminal_reason(
        done,
        process_exit=process_exit,
        stderr_task=stderr_task,
        overflow_waiter=overflow_waiter,
        stop_waiter=stop_waiter,
        stage="before_readiness",
    )
    if terminal is not None:
        await _cancel_probe(probe)
        if process_exit in done:
            await process_exit
        return ProbeAttempt(failure=terminal)
    try:
        return ProbeAttempt(client=probe.result())
    except RemoteTransportError as error:
        if type(error) is RemoteTransportError and error.args == ("remote_transport_unavailable",):
            return ProbeAttempt(retry_transport=True)
        return ProbeAttempt(failure=error)


async def wait_ready(
    process: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[StderrDrain],
    overflow: asyncio.Event,
    base_url: str,
    challenge: str,
    instance_nonce: str,
    *,
    policy: ObservationPolicy | None = None,
) -> RemoteClient:
    policy = policy or ObservationPolicy()
    process_exit = asyncio.create_task(process.wait())
    stop_waiter = asyncio.create_task(policy.stop.wait()) if policy.stop is not None else None
    overflow_waiter = asyncio.create_task(overflow.wait())
    active_probe: asyncio.Task[RemoteClient] | None = None
    ready_client: RemoteClient | None = None
    try:
        while True:
            terminal = _ready_state_failure(
                process, process_exit, stderr_task, overflow_waiter, stop_waiter
            )
            if terminal is not None:
                raise terminal
            active_probe = asyncio.create_task(_probe_api(base_url, challenge, instance_nonce))
            attempt = await _readiness_attempt(
                active_probe, process_exit, stderr_task, overflow_waiter, stop_waiter
            )
            active_probe = None
            if attempt.failure is not None:
                raise attempt.failure
            if attempt.client is not None:
                ready_client = attempt.client
                return ready_client
            if attempt.retry_transport:
                await asyncio.sleep(0.05)
                continue
            raise CommandIncompleteError("cleanup_unconfirmed", "execution")
    finally:
        cleanup_cancelled = False
        if active_probe is not None:
            try:
                await _cancel_probe(active_probe)
            except asyncio.CancelledError:
                cleanup_cancelled = True
        cleanup: list[asyncio.Task[object]] = [
            cast(asyncio.Task[object], process_exit),
            cast(asyncio.Task[object], overflow_waiter),
        ]
        if stop_waiter is not None:
            cleanup.append(cast(asyncio.Task[object], stop_waiter))
        _, cancelled = await _retained(asyncio.create_task(_cancel_tasks(cleanup)))
        if cancelled or cleanup_cancelled:
            await _retained(asyncio.create_task(_close_client(ready_client)))
            raise asyncio.CancelledError


async def _cleanup_probe(
    server: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[StderrDrain],
    overflow: asyncio.Event,
    client: RemoteClient | None,
) -> None:
    failure: BaseException | None = None
    try:
        await _stop_server(server, stderr_task, overflow)
    except BaseException as error:
        failure = error
    try:
        await _close_client(client)
    except BaseException as error:
        if failure is None:
            failure = error
    if failure is not None:
        raise failure


async def _run_remote_command(
    checked: Check,
    directory: Path,
    port: int,
    challenge: str,
    instance_nonce: str,
    server: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[StderrDrain],
    overflow: asyncio.Event,
    policy: ObservationPolicy | None,
) -> None:
    policy = policy or ObservationPolicy()

    async def execute_remote() -> str:
        return await checked(
            [
                str(python_in(directory / "remote")),
                "-I",
                "-c",
                REMOTE,
                f"http://127.0.0.1:{port}",
                challenge,
                instance_nonce,
            ],
            directory,
        )

    remote = asyncio.create_task(execute_remote())
    server_exit = asyncio.create_task(server.wait())
    stop_waiter = asyncio.create_task(policy.stop.wait()) if policy.stop is not None else None
    overflow_waiter = asyncio.create_task(overflow.wait())
    try:
        watched: set[asyncio.Future[object]] = {
            cast(asyncio.Future[object], remote),
            cast(asyncio.Future[object], server_exit),
            cast(asyncio.Future[object], stderr_task),
            cast(asyncio.Future[object], overflow_waiter),
        }
        if stop_waiter is not None:
            watched.add(cast(asyncio.Future[object], stop_waiter))
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        terminal = _terminal_reason(
            done,
            process_exit=server_exit,
            stderr_task=stderr_task,
            overflow_waiter=overflow_waiter,
            stop_waiter=stop_waiter,
            stage="during_remote_contract",
        )
        if terminal is not None:
            await _cancel_checked(remote)
            if server_exit in done:
                await server_exit
            raise terminal
        await remote
    finally:
        cleanup: list[asyncio.Task[object]] = [
            cast(asyncio.Task[object], server_exit),
            cast(asyncio.Task[object], overflow_waiter),
        ]
        if stop_waiter is not None:
            cleanup.append(cast(asyncio.Task[object], stop_waiter))
        _, cleanup_cancelled = await _retained(asyncio.create_task(_cancel_tasks(cleanup)))
        _, remote_cancelled = await _retained(asyncio.create_task(_cancel_checked(remote)))
        if cleanup_cancelled or remote_cancelled:
            raise asyncio.CancelledError


async def _cancel_checked(task: asyncio.Task[str]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def installed_remote(
    directory: Path,
    checked: Check,
    *,
    policy: ObservationPolicy | None = None,
) -> list[dict[str, str]]:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    challenge = secrets.token_urlsafe(32)
    instance_nonce = secrets.token_urlsafe(32)
    server = await _spawn_server(directory, port, challenge, instance_nonce)
    if server.stderr is None:
        await _retained(asyncio.create_task(_kill_and_reap(server)))
        raise CommandIncompleteError("cleanup_unconfirmed", "spawn")
    overflow = asyncio.Event()
    stderr_task = asyncio.create_task(drain_stderr(server.stderr, overflow))
    ready_client: RemoteClient | None = None
    primary: BaseException | None = None
    try:
        ready_client = await wait_ready(
            server,
            stderr_task,
            overflow,
            f"http://127.0.0.1:{port}",
            challenge,
            instance_nonce,
            policy=policy,
        )
        close_cancelled = await _close_client_retained(ready_client)
        ready_client = None
        if close_cancelled:
            raise asyncio.CancelledError
        await _run_remote_command(
            checked,
            directory,
            port,
            challenge,
            instance_nonce,
            server,
            stderr_task,
            overflow,
            policy,
        )
    except BaseException as error:
        primary = error
    finally:
        cleanup = asyncio.create_task(_cleanup_probe(server, stderr_task, overflow, ready_client))
        _, cancelled = await _retained(cleanup)
        if cancelled:
            raise asyncio.CancelledError
    if primary is not None:
        raise primary
    if server.returncode not in (0, -15):
        raise ValueError("installed_api_server_exit_status_nonzero")
    return [{"node_id": "installed::remote::separate-server-contract", "outcome": "passed"}]
