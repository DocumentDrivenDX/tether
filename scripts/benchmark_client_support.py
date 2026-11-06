"""Isolated process and terminal observations for the public-client benchmark."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import resource
import socket
import struct
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

from benchmark_client_environment import (
    PROJECT_ROOT as _PROJECT_ROOT,
)
from benchmark_client_environment import (
    _spawn_owned,
)
from benchmark_client_environment import (
    child_environment as _child_environment,
)
from benchmark_client_environment import (
    run_clean_capture as _run_clean_capture,
)
from benchmark_client_environment import (
    verified_tether_executable as _verified_tether_executable,
)
from benchmark_measurements import Distribution, distribution
from selected_profiles_process import await_retained

from tether.client import Actor, ClientHost, ClientProtocol, EntriesAppended, SessionUpdated

if TYPE_CHECKING:
    from tether.client import Credential

_READY_TIMEOUT_SECONDS = 30
_PROCESS_EVENT_TIMEOUT_SECONDS = 30
_FIRST_PAINT_TIMEOUT_SECONDS = 30
_MAX_TTY_CAPTURE_BYTES = 1024 * 1024
_FIRST_PAINT_MARKER = b"Send a fabricated message"
_AUTHORIZATION_VALUE = " ".join(("Bearer", "fabricated-local-benchmark"))
_SCRIPT_PATH = Path(__file__).resolve().with_name("benchmark_client.py")


def _distribution(values: list[float]) -> Distribution:
    return distribution(values)


async def first_output(
    root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    samples: int = 100,
    deadline_seconds: float = 30,
) -> dict[str, object]:
    """Measure the real local `tether list` command to its first output byte."""
    if samples < 100 or deadline_seconds <= 0:
        raise ValueError("benchmark_first_output_limits_invalid")
    child_environment = _child_environment(environment)
    executable = await _verified_tether_executable(child_environment)
    await _ensure_list_output(root)
    values = [
        await _first_output_sample(executable, root, deadline_seconds, child_environment)
        for _ in range(samples)
    ]
    return {
        "sample_count": samples,
        "spawn_to_first_byte": distribution(values),
        "argv": ["tether", "--root", "<fabricated-root>", "list"],
        "first_byte_observed": True,
        "clean_exit_samples": samples,
        "output_capture_limit_bytes": _MAX_TTY_CAPTURE_BYTES,
        "runtime_provenance": {
            "console_shebang_matches_active_interpreter": True,
            "child_import_matches_checkout_source": True,
        },
        "scope": "Actual local tether console entry point; spawn to first stdout byte, not --help.",
    }


async def _ensure_list_output(root: Path) -> None:
    from benchmark_client import settings

    async with await ClientHost.open(settings(root, 1, 1)) as host:
        # The production CLI's default demo principal is `demo`.
        client = host.client(Actor(id="demo"))
        page = await client.list()
        if not page.items:
            await client.create(spec="demo", title="Fabricated CLI startup")


async def _first_output_sample(
    executable: Path,
    root: Path,
    deadline_seconds: float,
    environment: Mapping[str, str] | None = None,
) -> float:
    owner = asyncio.create_task(
        _first_output_sample_owned(executable, root, deadline_seconds, environment)
    )
    return await await_retained(owner)


async def _first_output_sample_owned(
    executable: Path,
    root: Path,
    deadline_seconds: float,
    environment: Mapping[str, str] | None,
) -> float:
    started = time.perf_counter()
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(deadline_seconds):
            process = await _spawn_owned(
                (str(executable), "--root", str(root), "list"),
                environment or _child_environment(),
                stderr=asyncio.subprocess.DEVNULL,
            )
            if process.stdout is None:
                raise RuntimeError("benchmark_tether_stdout_unavailable")
            total = 0
            first = await process.stdout.read(1)
            if not first:
                raise RuntimeError("benchmark_tether_no_first_output")
            total = len(first)
            elapsed_ms = (time.perf_counter() - started) * 1000
            while chunk := await process.stdout.read(4096):
                total += len(chunk)
                if total > _MAX_TTY_CAPTURE_BYTES:
                    raise RuntimeError("benchmark_tether_output_limit")
            returncode = await process.wait()
            if returncode != 0:
                raise RuntimeError("benchmark_tether_list_failed")
            return elapsed_ms
    finally:
        if process is not None:
            await _stop_process(process)


async def child_startup(
    samples: int, *, environment: Mapping[str, str] | None = None
) -> dict[str, object]:
    child_environment = _child_environment(environment)
    result: dict[str, object] = {}
    for label, source in (
        ("core_import", "import tether"),
        ("cli_help", "from tether.cli.command import main; raise SystemExit(main(['--help']))"),
    ):
        values = []
        for _ in range(samples):
            started = time.perf_counter()
            returncode, _, _ = await _run_clean_capture(
                (sys.executable, "-c", source),
                environment=child_environment,
                timeout_seconds=30,
            )
            if returncode:
                raise RuntimeError("startup_child_failed:" + label)
            values.append((time.perf_counter() - started) * 1000)
        result[label] = _distribution(values)
    result["textual_first_paint"] = await textual_first_paint(
        samples, environment=child_environment
    )
    return result


async def textual_first_paint(
    samples: int, *, environment: Mapping[str, str] | None = None
) -> Distribution:
    if os.name != "posix":
        raise RuntimeError("textual_first_paint_requires_posix_pty")
    child_environment = _child_environment(environment)
    values = [await _textual_paint_sample(child_environment) for _ in range(samples)]
    return _distribution(values)


async def _textual_paint_sample(environment: Mapping[str, str]) -> float:
    import fcntl
    import pty
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from tether.cli import main; raise SystemExit(main(['--volatile']))",
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=_PROJECT_ROOT,
        env=_child_environment(environment, {"TERM": "xterm-256color"}),
    )
    os.close(slave)
    loop = asyncio.get_running_loop()
    painted: asyncio.Future[None] = loop.create_future()
    output = bytearray()

    def read_screen() -> None:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            if not painted.done():
                painted.set_exception(RuntimeError("textual_process_exited_before_first_paint"))
            return
        output.extend(chunk)
        if len(output) > _MAX_TTY_CAPTURE_BYTES and not painted.done():
            painted.set_exception(RuntimeError("textual_first_paint_capture_limit"))
        elif _FIRST_PAINT_MARKER in output and not painted.done():
            painted.set_result(None)

    loop.add_reader(master, read_screen)
    try:
        await asyncio.wait_for(painted, _FIRST_PAINT_TIMEOUT_SECONDS)
        return (time.perf_counter() - started) * 1000
    finally:
        loop.remove_reader(master)
        await _stop_process(process)
        os.close(master)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()


class _BenchmarkCredential:
    async def authorization(self) -> str:
        return _AUTHORIZATION_VALUE


class _BenchmarkCredentialProvider:
    async def __call__(self) -> Credential:
        return _BenchmarkCredential()


async def api_worker(root: Path, port: int) -> None:
    from benchmark_client import settings
    from fastapi import Request
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    from hypercorn.typing import ASGIFramework
    from tether_web_api import ApiFailure, ApiSettings, create_api

    class Authenticator:
        async def actor_for(self, request: Request) -> Actor:
            if request.headers.get("authorization") != _AUTHORIZATION_VALUE:
                raise ApiFailure(401, "authentication_required")
            return Actor(id="fabricated-benchmark")

    host = await ClientHost.open(settings(root, 1, 1))
    session = await host.client(Actor(id="fabricated-benchmark")).create(
        spec="demo", title="Fabricated cross-process feed"
    )
    app = create_api(
        clients=host.client,
        authenticator=Authenticator(),
        settings=ApiSettings(heartbeat_seconds=1),
    )

    async def announce_ready() -> None:
        print(json.dumps({"ready": True, "session_id": session.id}), flush=True)

    app.router.on_startup.append(announce_ready)
    config = Config()
    config.bind = [f"127.0.0.1:{port}"]
    config.accesslog = None
    config.errorlog = None
    config.graceful_timeout = 5
    try:
        await serve(cast(ASGIFramework, app), config)
    finally:
        await host.aclose()


async def follower_worker(url: str, session_id: str, since: int, samples: int) -> None:
    from tether.client.remote import RemoteClient, RemoteClientSettings

    async with await RemoteClient.open(
        RemoteClientSettings(url, _BenchmarkCredentialProvider())
    ) as client:
        print(json.dumps({"ready": True}), flush=True)
        received = 0
        async for event in client.follow(session_id=session_id, since=since):
            if not isinstance(event, (SessionUpdated, EntriesAppended)) or not event.last_in_commit:
                continue
            print(json.dumps({"seq": event.seq, "received_at": time.perf_counter()}), flush=True)
            received += 1
            if received == samples:
                print(json.dumps({"done": True, "peak_rss_bytes": peak_rss()}), flush=True)
                return


async def _read_json_line(process: asyncio.subprocess.Process) -> dict[str, object]:
    if process.stdout is None:
        raise RuntimeError("benchmark_worker_stdout_unavailable")
    try:
        line = await asyncio.wait_for(process.stdout.readline(), _READY_TIMEOUT_SECONDS)
    except TimeoutError:
        raise TimeoutError("benchmark_worker_did_not_respond") from None
    if not line:
        raise RuntimeError("benchmark_worker_exited_early")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("benchmark_worker_protocol_invalid")
    return value


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


async def separate_process_follower(
    samples: int, *, environment: Mapping[str, str] | None = None
) -> dict[str, object]:
    from tether.client.remote import RemoteClient, RemoteClientSettings

    child_environment = _child_environment(environment)
    with TemporaryDirectory(prefix="tether-cross-process-benchmark-") as directory:
        port = _free_port()
        server = await _launch_api_worker(Path(directory), port, child_environment)
        follower: asyncio.subprocess.Process | None = None
        try:
            ready = await _read_json_line(server)
            session_id = ready.get("session_id")
            if ready.get("ready") is not True or not isinstance(session_id, str):
                raise RuntimeError("benchmark_api_worker_not_ready")
            url = f"http://127.0.0.1:{port}"
            async with await RemoteClient.open(
                RemoteClientSettings(url, _BenchmarkCredentialProvider())
            ) as writer:
                baseline = await writer.snapshot(session_id)
                follower = await _launch_follower(
                    url, session_id, baseline.seq, samples, child_environment
                )
                follower_ready = await _read_json_line(follower)
                if follower_ready.get("ready") is not True:
                    raise RuntimeError("benchmark_follower_not_ready")
                deliveries = await _observe_follower(writer, follower, session_id, samples)
                receipt = await asyncio.wait_for(
                    _read_json_line(follower), _PROCESS_EVENT_TIMEOUT_SECONDS
                )
                if receipt.get("done") is not True:
                    raise RuntimeError("benchmark_follower_did_not_finish")
                return {
                    "profile": "local-posix-jsonl-http-api",
                    "followers": 1,
                    "commits": samples,
                    "command_to_follower_upper_bound": _distribution(deliveries),
                    "total_follower_process_peak_rss_bytes": receipt.get("peak_rss_bytes"),
                    "measurement": (
                        "Writer request start to last-in-commit delivery in a separate process; "
                        "an upper bound on commit-to-delivery, not an isolated segment."
                    ),
                }
        finally:
            await _stop_children(follower, server)


async def _launch_api_worker(
    root: Path, port: int, environment: Mapping[str, str]
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(_SCRIPT_PATH),
        "--worker",
        "api",
        "--root",
        str(root),
        "--port",
        str(port),
        cwd=_PROJECT_ROOT,
        env=dict(environment),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def _launch_follower(
    url: str,
    session_id: str,
    since: int,
    samples: int,
    environment: Mapping[str, str],
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(_SCRIPT_PATH),
        "--worker",
        "follow",
        "--url",
        url,
        "--session-id",
        session_id,
        "--since",
        str(since),
        "--worker-samples",
        str(samples),
        cwd=_PROJECT_ROOT,
        env=dict(environment),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def _observe_follower(
    writer: ClientProtocol,
    follower: asyncio.subprocess.Process,
    session_id: str,
    samples: int,
) -> list[float]:
    deliveries = []
    for index in range(samples):
        started = time.perf_counter()
        summary = await writer.update(session_id, title=f"Fabricated {index}")
        while True:
            message = await asyncio.wait_for(
                _read_json_line(follower), _PROCESS_EVENT_TIMEOUT_SECONDS
            )
            if message.get("done") is True:
                raise RuntimeError("benchmark_follower_stopped_early")
            if message.get("seq") == summary.last_seq:
                received_at = message.get("received_at")
                if not isinstance(received_at, (float, int)):
                    raise RuntimeError("benchmark_follower_timestamp_invalid")
                deliveries.append((float(received_at) - started) * 1000)
                break
    return deliveries


async def _stop_children(*children: asyncio.subprocess.Process | None) -> None:
    for process in children:
        if process is not None:
            await _stop_process(process)


def peak_rss() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)
