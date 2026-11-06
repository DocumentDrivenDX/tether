"""Real signal, connection drain and held physical work share one deadline."""

from __future__ import annotations

import asyncio
import json
import signal
import socket
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from tether_databricks import (
    CarrierASGI,
    DatabricksResources,
    ResourceLease,
    ResourceRef,
    ShutdownReport,
)

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_MARKERS = (
    (b"FABRICATED_LAUNCHER_MAIN_ENTERED", "launcher_main_entered"),
    (b"FABRICATED_LAUNCHER_MAIN_RETURNED", "launcher_main_returned"),
    (b"FABRICATED_LAUNCHER_MAIN_RAISED", "launcher_main_raised"),
    (b"FABRICATED_BUILD_ENTERED", "build_entered"),
    (b"FABRICATED_CHILD_STARTUP_ENTERED", "child_startup_entered"),
    (b"FABRICATED_SERVER_READY", "server_ready"),
    (b"FABRICATED_HTTP_CANCELLED", "http_cancelled"),
    (b"FABRICATED_CHILD_SHUTDOWN_ENTERED", "child_shutdown_entered"),
    (b"FABRICATED_LIFESPAN_SHUTDOWN", "lifespan_shutdown"),
    (b"TETHER_SHUTDOWN_REPORT ", "shutdown_report"),
)


async def test_absolute_deadline_only_tightens_before_shutdown_starts(monkeypatch) -> None:
    observed: list[float] = []

    class Lifecycle:
        async def close(self, *, timeout_seconds: float) -> ShutdownReport:
            observed.append(timeout_seconds)
            return ShutdownReport(complete=True, outstanding=(), closed=())

    async def unused_resource(ref: ResourceRef) -> ResourceLease:
        raise AssertionError("unexpected_resource_acquisition")

    async def child(scope, receive, send):
        while True:
            message = await receive()
            await send({"type": message["type"] + ".complete"})
            if message["type"] == "lifespan.shutdown":
                return

    app = CarrierASGI(
        api=child,
        ui=child,
        api_prefix="/api/v1",
        shutdown_timeout_seconds=100,
        lifecycle=Lifecycle(),
        resources=DatabricksResources.from_bindings((), acquire=unused_resource),
    )
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="invalid_shutdown_deadline"):
            app.limit_shutdown_deadline(invalid)
    clock = [100.0]
    monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
    app.limit_shutdown_deadline(110.0)
    app.limit_shutdown_deadline(105.0)
    app.limit_shutdown_deadline(115.0)
    messages = asyncio.Queue()
    messages.put_nowait({"type": "lifespan.startup"})
    messages.put_nowait({"type": "lifespan.shutdown"})
    sent = []

    async def send(message):
        sent.append(message["type"])

    await app({"type": "lifespan"}, messages.get, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert len(observed) == 1
    assert 0 < observed[0] <= 5
    with pytest.raises(RuntimeError, match="carrier_shutdown_started"):
        app.limit_shutdown_deadline(101.0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_CHILD = """
import asyncio
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Event

sys.path.insert(0, sys.argv[1])
import app as launcher
from tether_databricks import CarrierASGI, DatabricksAppSettings, ResourceLifecycle

def record_stage(stage):
    with Path(sys.argv[4]).open("a", encoding="utf-8") as stream:
        stream.write(stage + "\\n")
        stream.flush()

@dataclass(frozen=True)
class Bindings:
    settings: object
    api_factory: object = None
    ui_factory: object = None

async def child(scope, receive, send):
    global startup_count, shutdown_count
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                record_stage("child_startup_entered")
                print("FABRICATED_CHILD_STARTUP_ENTERED", flush=True)
                await send({"type": "lifespan.startup.complete"})
                startup_count += 1
                if startup_count == 2:
                    print("FABRICATED_SERVER_READY", flush=True)
            else:
                record_stage("child_shutdown_entered")
                print("FABRICATED_CHILD_SHUTDOWN_ENTERED", flush=True)
                await send({"type": "lifespan.shutdown.complete"})
                shutdown_count += 1
                if shutdown_count == 2:
                    print("FABRICATED_LIFESPAN_SHUTDOWN", flush=True)
                return
    else:
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: ready\\n\\n",
                    "more_body": True})
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            print("FABRICATED_HTTP_CANCELLED", flush=True)
            if sys.argv[3] == "hold":
                await release_request.wait()
            raise

startup_count = 0
shutdown_count = 0
release_request = asyncio.Event()

async def build(bindings, **factories):
    record_stage("build_entered")
    print("FABRICATED_BUILD_ENTERED", flush=True)
    event = Event()
    worker = asyncio.create_task(asyncio.to_thread(event.wait))
    async def close_host():
        await asyncio.shield(worker)
    async def close_database():
        raise AssertionError("physical_dependency_released_early")
    lifecycle = ResourceLifecycle({"client_host": close_host,
                                   "postgres_resources": close_database})
    return CarrierASGI(api=child, ui=child, api_prefix="/api/v1",
                       shutdown_timeout_seconds=bindings.settings.shutdown_timeout_seconds,
                       lifecycle=lifecycle, resources=None)

settings = DatabricksAppSettings(trusted_ingress="local", host="127.0.0.1",
                                port=int(sys.argv[2]), resource_bindings=(),
                                shutdown_timeout_seconds=1)
launcher._factory = lambda path: lambda: Bindings(settings)
launcher.build_reference_app = build
async def run():
    asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, release_request.set)
    record_stage("launcher_main_entered")
    print("FABRICATED_LAUNCHER_MAIN_ENTERED", flush=True)
    try:
        await launcher.main()
    except BaseException:
        record_stage("launcher_main_raised")
        print("FABRICATED_LAUNCHER_MAIN_RAISED", flush=True)
        raise
    else:
        record_stage("launcher_main_returned")
        print("FABRICATED_LAUNCHER_MAIN_RETURNED", flush=True)

asyncio.run(run())
"""


async def _read_output_lines(
    stream: asyncio.StreamReader, queue: asyncio.Queue, capture: dict[str, int | bool]
) -> None:
    try:
        while line := await stream.readline():
            capture["stdout_bytes"] = int(capture["stdout_bytes"]) + len(line)
            if int(capture["stdout_bytes"]) > 65536 and not capture["overflow"]:
                capture["overflow"] = True
                await queue.put(("overflow", int(capture["stdout_bytes"])))
            elif not capture["overflow"] and any(
                line.startswith(prefix) for prefix, _ in _OUTPUT_MARKERS
            ):
                await queue.put(("line", line))
            else:
                capture["other_lines"] = int(capture["other_lines"]) + 1
    except BaseException as error:
        await queue.put(("reader_error", type(error).__name__))
    finally:
        await queue.put(("eof", None))


async def _drain_stderr(stream: asyncio.StreamReader) -> tuple[int, bool]:
    observed_bytes = 0
    overflow = False
    try:
        while chunk := await stream.read(8192):
            observed_bytes += len(chunk)
            overflow = overflow or observed_bytes > 65536
    except BaseException:
        return observed_bytes, True
    return observed_bytes, overflow


async def _next_output_event(
    queue: asyncio.Queue,
    process_terminal: asyncio.Task[int],
    output_reader: asyncio.Task[None],
) -> tuple[str, bytes | str | None]:
    while True:
        next_line = asyncio.create_task(queue.get())
        try:
            watched: set[asyncio.Task] = {next_line, output_reader}
            if not process_terminal.done():
                watched.add(process_terminal)
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if next_line in done:
                return next_line.result()
            if output_reader in done:
                # Reader completion follows its queued EOF/error event.
                next_line.cancel()
                await asyncio.gather(next_line, return_exceptions=True)
                return await queue.get()
            # Process completion does not imply all buffered stdout was read.
        finally:
            if not next_line.done():
                next_line.cancel()
            await asyncio.gather(next_line, return_exceptions=True)


async def _next_output_line(
    queue: asyncio.Queue,
    process_terminal: asyncio.Task[int],
    output_reader: asyncio.Task[None],
    observed: list[str],
    *,
    allow_main_returned: bool = False,
) -> bytes:
    event, value = await _next_output_event(queue, process_terminal, output_reader)
    if event == "process_terminal":
        raise AssertionError(f"fabricated_carrier_exited_before_state:{value}")
    if event == "reader_error":
        raise AssertionError(f"fabricated_carrier_output_reader_failed:{value}")
    if event == "overflow":
        raise AssertionError(f"fabricated_carrier_stdout_capture_exceeded:{value}")
    if event == "eof":
        raise AssertionError("fabricated_carrier_output_ended_before_state")
    line = value
    assert isinstance(line, bytes)
    observed.extend(name for prefix, name in _OUTPUT_MARKERS if line.startswith(prefix))
    if len(observed) > 64:
        raise AssertionError("fabricated_carrier_too_many_state_markers")
    if line.startswith(b"FABRICATED_LAUNCHER_MAIN_RAISED"):
        raise AssertionError("fabricated_launcher_main_raised_before_state")
    if line.startswith(b"FABRICATED_LAUNCHER_MAIN_RETURNED") and not allow_main_returned:
        raise AssertionError("fabricated_launcher_main_ended_before_state")
    return line


async def _wait_for_output(
    queue: asyncio.Queue,
    expected: bytes,
    process_terminal: asyncio.Task[int],
    output_reader: asyncio.Task[None],
    observed: list[str],
) -> bytes:
    while True:
        line = await _next_output_line(
            queue,
            process_terminal,
            output_reader,
            observed,
            allow_main_returned=expected == b"FABRICATED_LAUNCHER_MAIN_RETURNED",
        )
        if line.startswith(expected):
            return line


async def _connect_when_listening(
    client: Any,
    url: str,
    queue: asyncio.Queue,
    process_terminal: asyncio.Task[int],
    output_reader: asyncio.Task[None],
    observed: list[str],
) -> httpx.Response:
    state = asyncio.create_task(_next_output_line(queue, process_terminal, output_reader, observed))
    request: asyncio.Task[httpx.Response] | None = None
    retry: asyncio.Task[None] | None = None
    returned: httpx.Response | None = None
    try:
        while True:
            request = _start_request(client, url, request, retry)
            done = await _wait_connection_events(state, request, retry)
            state = _advance_output_state(
                state, done, queue, process_terminal, output_reader, observed
            )
            if request is not None and request in done:
                try:
                    returned = request.result()
                except httpx.ConnectError:
                    request = None
                    retry = asyncio.create_task(asyncio.sleep(0.05))
                else:
                    return returned
            if retry is not None and retry in done:
                retry.result()
                retry = None
    finally:
        await _settle_connection_observers(state, request, retry, returned)


def _start_request(
    client: Any,
    url: str,
    request: asyncio.Task[httpx.Response] | None,
    retry: asyncio.Task[None] | None,
) -> asyncio.Task[httpx.Response] | None:
    if request is None and retry is None:
        return asyncio.create_task(client.send(client.build_request("GET", url), stream=True))
    return request


async def _wait_connection_events(
    state: asyncio.Task[bytes],
    request: asyncio.Task[httpx.Response] | None,
    retry: asyncio.Task[None] | None,
) -> set[asyncio.Task]:
    watched: set[asyncio.Task] = {state}
    if request is not None:
        watched.add(request)
    if retry is not None:
        watched.add(retry)
    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
    return done


def _advance_output_state(
    state: asyncio.Task[bytes],
    done: set[asyncio.Task],
    queue: asyncio.Queue,
    process_terminal: asyncio.Task[int],
    output_reader: asyncio.Task[None],
    observed: list[str],
) -> asyncio.Task[bytes]:
    if state not in done:
        return state
    state.result()
    return asyncio.create_task(_next_output_line(queue, process_terminal, output_reader, observed))


async def _settle_connection_observers(
    state: asyncio.Task[bytes],
    request: asyncio.Task[httpx.Response] | None,
    retry: asyncio.Task[None] | None,
    returned: httpx.Response | None,
) -> None:
    tasks = [
        state,
        *([request] if request is not None else []),
        *([retry] if retry is not None else []),
    ]
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if request is None or not request.done() or request.cancelled():
        return
    with suppress(Exception):
        response = request.result()
        if response is not returned:
            await response.aclose()


def _assert_shutdown_report(line: bytes) -> None:
    report = json.loads(line.partition(b" ")[2])
    assert report["signal_name"] == "SIGTERM"
    assert 0 <= report["signal_to_report_seconds"] <= report["configured_shutdown_seconds"]
    assert report["configured_shutdown_seconds"] == 1
    assert "1.1" in report["asgi_http_versions"]
    assert report["complete"] is False
    assert set(report["outstanding"]) == {"client_host", "postgres_resources"}
    assert {"api", "ui"} <= set(report["closed"])


def _prepare_child(tmp_path: Path) -> tuple[Path, int, Path]:
    child = tmp_path / "launcher.py"
    child.write_text(_CHILD)
    child.chmod(0o600)
    progress = tmp_path / "launcher-progress.txt"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    progress.write_text("prepared\n", encoding="utf-8")
    progress.chmod(0o600)
    return child, port, progress


@pytest.mark.parametrize("hold_request", [False, True], ids=["normal-drain", "held-cancel-drain"])
async def test_signal_report_names_retained_worker_before_total_deadline(
    tmp_path: Path, hold_request: bool
) -> None:
    """@covers US-068-AC8 Local signal evidence; selected Apps remains separate."""
    child, port, progress = await asyncio.to_thread(_prepare_child, tmp_path)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(child),
        str(ROOT / "examples/databricks-app/src"),
        str(port),
        "hold" if hold_request else "release",
        str(progress),
        env={"TETHER_APP_RUNTIME": "fabricated:factory"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    output = asyncio.Queue(maxsize=32)
    stdout_capture: dict[str, int | bool] = {
        "stdout_bytes": 0,
        "other_lines": 0,
        "overflow": False,
    }
    output_reader = asyncio.create_task(_read_output_lines(process.stdout, output, stdout_capture))
    stderr_reader = asyncio.create_task(_drain_stderr(process.stderr))
    process_terminal = asyncio.create_task(process.wait())
    observed: list[str] = []
    response = None
    try:
        await _wait_for_output(
            output, b"FABRICATED_SERVER_READY", process_terminal, output_reader, observed
        )
        # State markers, not a request timer, decide when this test advances.
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:  # noqa: S113
            response = await _connect_when_listening(
                client,
                f"http://127.0.0.1:{port}/api/v1/events",
                output,
                process_terminal,
                output_reader,
                observed,
            )
            assert response.status_code == 200
            assert await anext(response.aiter_bytes()) == b"data: ready\n\n"
            process.send_signal(signal.SIGTERM)
            await _wait_for_output(
                output, b"FABRICATED_HTTP_CANCELLED", process_terminal, output_reader, observed
            )
            if hold_request:
                process.send_signal(signal.SIGUSR1)
            line = await _wait_for_output(
                output, b"TETHER_SHUTDOWN_REPORT ", process_terminal, output_reader, observed
            )
            _assert_shutdown_report(line)
            await _wait_for_output(
                output,
                b"FABRICATED_LAUNCHER_MAIN_RETURNED",
                process_terminal,
                output_reader,
                observed,
            )
            assert process.returncode is None
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process_terminal
        if response is not None:
            await response.aclose()
        await output_reader
        stderr_bytes, stderr_overflow = await stderr_reader
        assert not stdout_capture["overflow"]
        assert not stderr_overflow, f"fabricated_stderr_capture_exceeded:{stderr_bytes}"
