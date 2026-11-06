"""Installed API observation follows exact process, stream and request state."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from typing import cast

import check_web_packages
import httpx
import pytest
import web_remote_capture
import web_remote_probe
from process import CommandIncompleteError, Result
from selected_profiles_process import await_retained

from tether.client.remote import RemoteClient, RemoteProblem
from tether.client.remote.errors import RemoteTransportError


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeProcess:
    def __init__(self, stderr: object, returncode: int | None = None):
        self.stderr = stderr
        self.returncode = returncode
        self.exited = asyncio.Event()
        self.terminated_event = asyncio.Event()
        self.reap_release = asyncio.Event()
        self.hold_reap = False
        self.terminated = False
        self.killed = False
        self._transport: object | None = None
        if returncode is not None:
            self.exited.set()

    async def wait(self) -> int:
        await self.exited.wait()
        if self.hold_reap:
            await self.reap_release.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.terminated_event.set()
        self.returncode = -15
        self.exited.set()
        feed_eof = getattr(self.stderr, "feed_eof", None)
        if callable(feed_eof):
            feed_eof()

    def kill(self) -> None:
        self.killed = True
        self.terminated_event.set()
        self.returncode = -9
        self.exited.set()
        feed_eof = getattr(self.stderr, "feed_eof", None)
        if callable(feed_eof):
            feed_eof()


class ReadyClient:
    def __init__(self):
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def settle_task(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def wait_event_or_owner(event: asyncio.Event, owner: asyncio.Task[object]) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait((waiter, owner), return_when=asyncio.FIRST_COMPLETED)
        if owner in done and waiter not in done:
            await owner
            pytest.fail("probe owner completed before its event")
        assert waiter.result()
    finally:
        await await_retained(asyncio.create_task(settle_task(cast(asyncio.Task[object], waiter))))


@pytest.mark.anyio
async def test_exited_server_is_refused_before_api_probe(monkeypatch):
    reader = asyncio.StreamReader()
    reader.feed_eof()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader, returncode=1))
    overflow = asyncio.Event()
    stderr_task = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))

    async def unexpected_probe(*_args):
        raise AssertionError("an exited server cannot become ready")

    monkeypatch.setattr(web_remote_probe, "_probe_api", unexpected_probe)
    with pytest.raises(ValueError, match="server_exited_before_readiness"):
        await web_remote_probe.wait_ready(
            process, stderr_task, overflow, "http://127.0.0.1:1234", "challenge", "instance"
        )
    assert stderr_task.result().read_failed is False


@pytest.mark.anyio
async def test_readiness_retries_only_typed_transport_and_uses_challenge(monkeypatch):
    reader = asyncio.StreamReader()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader))
    overflow = asyncio.Event()
    stderr_task = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    attempts: list[tuple[str, str, str]] = []

    async def probe(base_url: str, challenge: str, nonce: str) -> RemoteClient:
        attempts.append((base_url, challenge, nonce))
        if len(attempts) == 1:
            raise RemoteTransportError("remote_transport_unavailable")
        return cast(RemoteClient, ReadyClient())

    monkeypatch.setattr(web_remote_probe, "_probe_api", probe)
    try:
        client = await web_remote_probe.wait_ready(
            process, stderr_task, overflow, "http://127.0.0.1:1234", "run-scoped-challenge", "nonce"
        )
        assert isinstance(client, ReadyClient)
        assert attempts == [
            ("http://127.0.0.1:1234", "run-scoped-challenge", "nonce"),
            ("http://127.0.0.1:1234", "run-scoped-challenge", "nonce"),
        ]
    finally:
        process.terminate()
        await web_remote_probe._stop_server(process, stderr_task, overflow)


@pytest.mark.anyio
async def test_readiness_refusal_is_not_retried_and_watchdog_is_incomplete(monkeypatch):
    reader = asyncio.StreamReader()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader))
    overflow = asyncio.Event()
    stderr_task = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    attempts = 0

    async def refuse(*_args):
        nonlocal attempts
        attempts += 1
        raise ValueError("authentication_required")

    monkeypatch.setattr(web_remote_probe, "_probe_api", refuse)
    try:
        with pytest.raises(ValueError, match="authentication_required"):
            await web_remote_probe.wait_ready(
                process, stderr_task, overflow, "http://127.0.0.1:1234", "challenge", "nonce"
            )
        assert attempts == 1

        async def invalid_response(*_args):
            nonlocal attempts
            attempts += 1
            raise RemoteTransportError("remote_response_invalid_json")

        monkeypatch.setattr(web_remote_probe, "_probe_api", invalid_response)
        with pytest.raises(RemoteTransportError, match="remote_response_invalid_json"):
            await web_remote_probe.wait_ready(
                process, stderr_task, overflow, "http://127.0.0.1:1234", "challenge", "nonce"
            )
        assert attempts == 2

        entered = asyncio.Event()

        async def blocked(*_args):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(web_remote_probe, "_probe_api", blocked)
        stop = asyncio.Event()
        operation = asyncio.create_task(
            web_remote_probe.wait_ready(
                process,
                stderr_task,
                overflow,
                "http://127.0.0.1:1234",
                "challenge",
                "nonce",
                policy=web_remote_probe.ObservationPolicy(stop=stop),
            )
        )
        await wait_event_or_owner(entered, cast(asyncio.Task[object], operation))
        stop.set()
        with pytest.raises(CommandIncompleteError, match="watchdog_exhausted"):
            await operation
    finally:
        process.terminate()
        await web_remote_probe._stop_server(process, stderr_task, overflow)


@pytest.mark.anyio
async def test_stderr_overflow_is_signaled_while_reader_keeps_draining(monkeypatch):
    monkeypatch.setattr(web_remote_capture, "MAX_SERVER_STDERR_BYTES", 4)
    reader = asyncio.StreamReader()
    reader.feed_data(b"over-limit")
    overflow = asyncio.Event()
    drain = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    overflow_waiter = asyncio.create_task(overflow.wait())
    try:
        done, _ = await asyncio.wait((drain, overflow_waiter), return_when=asyncio.FIRST_COMPLETED)
        assert overflow_waiter in done
        assert not drain.done()
    finally:
        reader.feed_eof()
        await asyncio.gather(drain, return_exceptions=True)
        await settle_task(cast(asyncio.Task[object], overflow_waiter))
    assert drain.result().overflowed
    assert drain.result().bytes_observed == len(b"over-limit")


@pytest.mark.anyio
async def test_failed_stderr_reader_is_replaced_only_after_terminal_before_reap():
    class FailOnceReader:
        def __init__(self):
            self.calls = 0
            self.active = 0
            self.max_active = 0

        async def read(self, _size: int) -> bytes:
            self.active += 1
            self.max_active = max(self.active, self.max_active)
            try:
                self.calls += 1
                if self.calls == 1:
                    raise OSError("fabricated stream canary")
                return b"" if self.calls > 2 else b"discard me"
            finally:
                self.active -= 1

    stream = FailOnceReader()
    fake_process = FakeProcess(stream)
    process = cast(asyncio.subprocess.Process, fake_process)
    overflow = asyncio.Event()
    initial = asyncio.create_task(
        web_remote_probe.drain_stderr(cast(asyncio.StreamReader, stream), overflow)
    )
    first = await initial
    assert first.read_failed
    with pytest.raises(CommandIncompleteError, match="output_capture_failed"):
        await web_remote_probe._stop_server(process, initial, overflow)
    assert fake_process.terminated
    assert await process.wait() == -15
    assert stream.calls == 3
    assert stream.max_active == 1


@pytest.mark.anyio
async def test_cancelled_stderr_reader_gets_exclusive_fallback_then_stays_incomplete():
    class HeldReader:
        def __init__(self):
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.active = 0
            self.max_active = 0

        async def read(self, _size: int) -> bytes:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.active, self.max_active)
            try:
                if self.calls == 1:
                    self.entered.set()
                    await self.release.wait()
                    return b""
                if self.calls == 2:
                    self.entered.set()
                    await self.release.wait()
                    raise OSError("fabricated fallback canary")
                return b""
            finally:
                self.active -= 1

    stream = HeldReader()
    fake_process = FakeProcess(stream)
    process = cast(asyncio.subprocess.Process, fake_process)
    overflow = asyncio.Event()
    initial = asyncio.create_task(
        web_remote_probe.drain_stderr(cast(asyncio.StreamReader, stream), overflow)
    )
    cleanup = None
    try:
        await wait_event_or_owner(stream.entered, cast(asyncio.Task[object], initial))
        initial.cancel()
        with pytest.raises(asyncio.CancelledError):
            await initial
        stream.entered.clear()
        cleanup = asyncio.create_task(web_remote_probe._stop_server(process, initial, overflow))
        await wait_event_or_owner(stream.entered, cast(asyncio.Task[object], cleanup))
        assert fake_process.terminated
        assert not cleanup.done()
        stream.release.set()
        with pytest.raises(CommandIncompleteError, match="output_capture_failed"):
            await cleanup
        assert fake_process.terminated
        assert await process.wait() == -15
        assert stream.max_active == 1
    finally:
        stream.release.set()
        if not initial.done():
            initial.cancel()
            await asyncio.gather(initial, return_exceptions=True)
        if cleanup is not None:
            if process.returncode is None:
                fake_process.terminate()
            with suppress(CommandIncompleteError):
                await await_retained(cleanup)
        elif process.returncode is None:
            fake_process.terminate()


@pytest.mark.anyio
async def test_reader_failure_during_reap_starts_fallback_before_child_settlement():
    class FailThenHoldReader:
        def __init__(self):
            self.calls = 0
            self.reader_entered = asyncio.Event()
            self.fail_release = asyncio.Event()
            self.fallback_entered = asyncio.Event()
            self.fallback_release = asyncio.Event()
            self.active = 0
            self.max_active = 0

        async def read(self, _size: int) -> bytes:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if self.calls == 1:
                    self.reader_entered.set()
                    await self.fail_release.wait()
                    raise OSError("fabricated original reader failure")
                if self.calls == 2:
                    self.fallback_entered.set()
                    await self.fallback_release.wait()
                    return b"discarded fallback bytes"
                return b""
            finally:
                self.active -= 1

    stream = FailThenHoldReader()
    fake_process = FakeProcess(stream)
    fake_process.hold_reap = True
    process = cast(asyncio.subprocess.Process, fake_process)
    overflow = asyncio.Event()
    initial = asyncio.create_task(
        web_remote_probe.drain_stderr(cast(asyncio.StreamReader, stream), overflow)
    )
    cleanup = None
    try:
        await wait_event_or_owner(stream.reader_entered, cast(asyncio.Task[object], initial))
        cleanup = asyncio.create_task(web_remote_probe._stop_server(process, initial, overflow))
        stream.fail_release.set()
        await wait_event_or_owner(stream.fallback_entered, cast(asyncio.Task[object], cleanup))
        assert not cleanup.done()
        assert not fake_process.reap_release.is_set()
        stream.fallback_release.set()
        fake_process.reap_release.set()
        with pytest.raises(CommandIncompleteError, match="output_capture_failed"):
            await cleanup
        assert stream.max_active == 1
        assert stream.calls == 3
    finally:
        stream.fail_release.set()
        stream.fallback_release.set()
        fake_process.reap_release.set()
        if cleanup is not None:
            with suppress(CommandIncompleteError):
                await await_retained(cleanup)
        elif not initial.done():
            initial.cancel()
            await asyncio.gather(initial, return_exceptions=True)


@pytest.mark.anyio
async def test_failed_fallback_closes_owned_pipe_before_waiting_for_dead_leader():
    class FailedReader:
        def __init__(self):
            self.calls = 0

        async def read(self, _size: int) -> bytes:
            self.calls += 1
            raise OSError("fabricated persistent capture failure")

    class Pipe:
        def __init__(self, release: asyncio.Event):
            self.release = release
            self.closed = False

        def close(self):
            self.closed = True
            self.release.set()

    class Transport:
        def __init__(self, pipe: Pipe):
            self.pipe = pipe

        def get_pipe_transport(self, descriptor: int):
            return self.pipe if descriptor == 2 else None

    stream = FailedReader()
    fake_process = FakeProcess(stream, returncode=-15)
    fake_process.hold_reap = True
    pipe = Pipe(fake_process.reap_release)
    fake_process._transport = Transport(pipe)
    process = cast(asyncio.subprocess.Process, fake_process)
    overflow = asyncio.Event()
    initial = asyncio.create_task(
        web_remote_probe.drain_stderr(cast(asyncio.StreamReader, stream), overflow)
    )
    await initial
    with pytest.raises(CommandIncompleteError, match="output_capture_failed"):
        await web_remote_probe._stop_server(process, initial, overflow)
    assert pipe.closed
    assert fake_process.reap_release.is_set()
    assert await process.wait() == -15
    assert stream.calls == 2


@pytest.mark.anyio
async def test_cancel_during_readiness_cleanup_closes_ready_client(monkeypatch):
    reader = asyncio.StreamReader()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader))
    overflow = asyncio.Event()
    stderr = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    ready = ReadyClient()
    entered, release = asyncio.Event(), asyncio.Event()
    original_cleanup = web_remote_probe._cancel_tasks

    async def hold_cleanup(tasks):
        entered.set()
        await release.wait()
        await original_cleanup(tasks)

    async def probe(*_args):
        return cast(RemoteClient, ready)

    monkeypatch.setattr(web_remote_probe, "_cancel_tasks", hold_cleanup)
    monkeypatch.setattr(web_remote_probe, "_probe_api", probe)
    operation = asyncio.create_task(
        web_remote_probe.wait_ready(process, stderr, overflow, "http://local", "challenge", "nonce")
    )
    try:
        await wait_event_or_owner(entered, cast(asyncio.Task[object], operation))
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert ready.closed
    finally:
        release.set()
        process.terminate()
        await web_remote_probe._stop_server(process, stderr, overflow)


@pytest.mark.anyio
async def test_cancel_during_remote_cleanup_propagates_after_owned_tasks_settle(
    monkeypatch, tmp_path
):
    reader = asyncio.StreamReader()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader))
    overflow = asyncio.Event()
    stderr = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    entered, release = asyncio.Event(), asyncio.Event()
    original_cleanup = web_remote_probe._cancel_tasks

    async def hold_cleanup(tasks):
        entered.set()
        await release.wait()
        await original_cleanup(tasks)

    async def checked(*_args):
        return "complete"

    monkeypatch.setattr(web_remote_probe, "_cancel_tasks", hold_cleanup)
    operation = asyncio.create_task(
        web_remote_probe._run_remote_command(
            checked, tmp_path, 1234, "challenge", "nonce", process, stderr, overflow, None
        )
    )
    try:
        await wait_event_or_owner(entered, cast(asyncio.Task[object], operation))
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    finally:
        release.set()
        process.terminate()
        await web_remote_probe._stop_server(process, stderr, overflow)


@pytest.mark.anyio
async def test_installed_probe_retains_ready_client_close_across_repeated_cancel(
    monkeypatch, tmp_path
):
    class Reservation:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, _address):
            return None

        def getsockname(self):
            return "127.0.0.1", 12345

    class HeldCloseClient:
        def __init__(self):
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.calls = 0
            self.closed = False

        async def aclose(self):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            self.closed = True

    reader = asyncio.StreamReader()
    fake_process = FakeProcess(reader)
    ready = HeldCloseClient()

    async def spawn(*_args, **_kwargs):
        return fake_process

    async def wait_ready(*_args, **_kwargs):
        return cast(RemoteClient, ready)

    async def unexpected_remote(*_args, **_kwargs):
        raise AssertionError("cancellation during client close must stop before remote work")

    monkeypatch.setattr(web_remote_probe.socket, "socket", Reservation)
    monkeypatch.setattr(web_remote_probe, "_spawn_server", spawn)
    monkeypatch.setattr(web_remote_probe, "wait_ready", wait_ready)
    monkeypatch.setattr(web_remote_probe, "_run_remote_command", unexpected_remote)
    operation = asyncio.create_task(web_remote_probe.installed_remote(tmp_path, unexpected_remote))
    try:
        await wait_event_or_owner(ready.entered, cast(asyncio.Task[object], operation))
        operation.cancel()
        operation.cancel()
        ready.release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert ready.closed
        assert ready.calls == 1
        assert fake_process.terminated
    finally:
        ready.release.set()
        fake_process.terminate()
        operation.cancel()
        with suppress(asyncio.CancelledError):
            await await_retained(operation)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("challenge", "witness", "failure"),
    [
        ("expected", "instance-1", None),
        ("expected", "foreign", "mismatch"),
        ("wrong", "instance-1", "auth"),
    ],
)
async def test_readiness_authenticates_and_correlates_capabilities_response(
    monkeypatch, challenge, witness, failure
):
    reader = asyncio.StreamReader()
    process = cast(asyncio.subprocess.Process, FakeProcess(reader))
    overflow = asyncio.Event()
    stderr = asyncio.create_task(web_remote_probe.drain_stderr(reader, overflow))
    observed: list[str | None] = []
    transports: list[httpx.AsyncClient] = []
    original_http_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.headers.get("authorization"))
        assert request.headers.get("x-tether-instance") is None
        if challenge == "wrong" and request.headers.get("authorization") != "Bearer expected":
            return httpx.Response(401, json={"code": "authentication_required"})
        return httpx.Response(
            200,
            json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            headers={"x-tether-instance": witness},
        )

    def mock_transport_client(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        kwargs.setdefault("trust_env", False)
        client = original_http_client(*args, **kwargs)
        transports.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", mock_transport_client)
    try:
        if failure is None:
            client = await web_remote_probe.wait_ready(
                process, stderr, overflow, "http://installed.test", challenge, "instance-1"
            )
            await client.aclose()
            assert observed == ["Bearer expected", "Bearer expected"]
        elif failure == "auth":
            with pytest.raises(RemoteProblem, match="authentication_required"):
                await web_remote_probe.wait_ready(
                    process, stderr, overflow, "http://installed.test", challenge, "instance-1"
                )
            assert observed == ["Bearer wrong"]
        else:
            with pytest.raises(ValueError, match="installed_api_instance_mismatch"):
                await web_remote_probe.wait_ready(
                    process, stderr, overflow, "http://installed.test", challenge, "instance-1"
                )
            assert observed == ["Bearer expected", "Bearer expected"]
    finally:
        process.terminate()
        await web_remote_probe._stop_server(process, stderr, overflow)
        for transport in transports:
            await transport.aclose()


@pytest.mark.anyio
async def test_spawn_cancellation_reaps_exact_returned_server(monkeypatch, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    reader = asyncio.StreamReader()
    process = FakeProcess(reader)
    process.hold_reap = True

    async def delayed_spawn(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(
        web_remote_probe._spawn_server(tmp_path, 1234, "challenge", "instance")
    )
    try:
        await wait_event_or_owner(entered, cast(asyncio.Task[object], task))
        task.cancel()
        release.set()
        await wait_event_or_owner(process.terminated_event, cast(asyncio.Task[object], task))
        assert not task.done()
        task.cancel()
        process.reap_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        process.reap_release.set()
        if not task.done():
            task.cancel()
        await await_retained(asyncio.create_task(settle_task(cast(asyncio.Task[object], task))))
    assert process.terminated
    assert process.returncode == -15


@pytest.mark.anyio
async def test_installed_probe_uses_one_challenge_for_readiness_and_remote_contract(
    monkeypatch, tmp_path
):
    class PortReservation:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, _address):
            return None

        def getsockname(self):
            return "127.0.0.1", 12345

    reader = asyncio.StreamReader()
    process = FakeProcess(reader)
    challenge_seen: list[str] = []
    nonce_seen: list[str] = []
    checked_args: list[list[str]] = []

    async def spawn(*args, **_kwargs):
        challenge_seen.append(str(args[-2]))
        nonce_seen.append(str(args[-1]))
        return process

    async def probe(_base_url: str, challenge: str, nonce: str) -> RemoteClient:
        assert challenge == challenge_seen[0]
        assert nonce == nonce_seen[0]
        return cast(RemoteClient, ReadyClient())

    async def checked(arguments: list[str], _cwd: Path) -> str:
        checked_args.append(arguments)
        return "complete"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(web_remote_probe.socket, "socket", PortReservation)
    monkeypatch.setattr(web_remote_probe, "_probe_api", probe)
    result = await web_remote_probe.installed_remote(tmp_path, checked)
    assert result == [
        {"node_id": "installed::remote::separate-server-contract", "outcome": "passed"}
    ]
    assert challenge_seen
    assert checked_args[0][-2:] == [challenge_seen[0], nonce_seen[0]]
    assert "sys.argv[2]" in web_remote_probe.SERVER
    assert "sys.argv[2]" in web_remote_probe.REMOTE
    assert "sys.argv[3]" in web_remote_probe.SERVER
    assert "sys.argv[3]" in web_remote_probe.REMOTE
    assert process.terminated


@pytest.mark.anyio
async def test_package_main_maps_typed_incomplete_to_exit_two(monkeypatch, capsys):
    async def incomplete(*_args):
        raise CommandIncompleteError("watchdog_exhausted", "execution")

    monkeypatch.setattr(check_web_packages, "run", incomplete)
    monkeypatch.setattr(sys, "argv", ["check_web_packages.py"])
    assert await check_web_packages.main() == 2
    output = capsys.readouterr()
    assert "watchdog_exhausted" in output.err
    assert "secret" not in output.err


@pytest.mark.anyio
async def test_remote_failure_diagnostics_are_closed(monkeypatch, tmp_path):
    async def failed(*_args, **_kwargs):
        return Result(returncode=1, stdout="stdout private canary", stderr="stderr private canary")

    monkeypatch.setattr(check_web_packages, "run_command", failed)
    with pytest.raises(ValueError, match="installed_remote_command_failed"):
        await check_web_packages.checked_remote(["/python", "-c", "pass"], tmp_path)
