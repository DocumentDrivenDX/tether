"""Loopback-only fabricated browser fixture using the real public client and API."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar, cast

from fastapi import Request
from fixture import browser_host
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from starlette.applications import Starlette
from starlette.routing import Mount
from tether_web_api import ApiSettings, create_api
from tether_web_ui import UiSettings, create_static_app

from tether.client import Actor

SERVER_SCRIPT = Path(__file__).resolve()
JSONL_LEASE_SECONDS = 30
FUNCTIONAL_WATCHDOG_ENV = "TETHER_FUNCTIONAL_WATCHDOG_MS"
DEFAULT_FUNCTIONAL_WATCHDOG_MS = 30 * 60 * 1000
MAX_FUNCTIONAL_WATCHDOG_MS = 2**31 - 1
SEED_STDOUT_LIMIT = 64 * 1024
WATCHDOG_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
T = TypeVar("T")


class BrowserFixtureIncomplete(RuntimeError):
    def __init__(self, reason: str, phase: str) -> None:
        self.reason = reason
        self.phase = phase
        super().__init__(f"browser_fixture_{reason}")


def functional_watchdog_seconds(values: Mapping[str, str]) -> float:
    raw = values.get(FUNCTIONAL_WATCHDOG_ENV)
    if raw is None:
        milliseconds = DEFAULT_FUNCTIONAL_WATCHDOG_MS
    else:
        if not WATCHDOG_DECIMAL.fullmatch(raw):
            raise RuntimeError("functional_watchdog_invalid")
        try:
            milliseconds = int(raw)
        except ValueError:
            raise RuntimeError("functional_watchdog_invalid") from None
    if not 1_000 <= milliseconds <= MAX_FUNCTIONAL_WATCHDOG_MS:
        raise RuntimeError("functional_watchdog_invalid")
    return milliseconds / 1000


def _signal_seed_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        raise BrowserFixtureIncomplete("cleanup_unconfirmed", "cleanup") from None


async def _retained(task: asyncio.Task[T]) -> tuple[T, bool]:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def _stop_seed_process(
    process: asyncio.subprocess.Process,
    process_done: asyncio.Task[int],
    output_drain: asyncio.Task[bool],
) -> None:
    # The seed has no subprocess tools; it inherits its owning browser/npm job group.
    _signal_seed_process(process)
    drain_result = (await asyncio.gather(output_drain, return_exceptions=True))[0]
    if isinstance(drain_result, BaseException):
        if process.stdout is None:
            raise BrowserFixtureIncomplete("cleanup_unconfirmed", "cleanup") from None
        replacement_drain = asyncio.create_task(_discard_seed_output(process.stdout))
        results = await asyncio.gather(process_done, replacement_drain, return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise BrowserFixtureIncomplete("cleanup_unconfirmed", "cleanup") from None
        raise BrowserFixtureIncomplete("output_reader_failed", "cleanup") from None
    process_result = (await asyncio.gather(process_done, return_exceptions=True))[0]
    if isinstance(process_result, BaseException):
        raise BrowserFixtureIncomplete("cleanup_unconfirmed", "cleanup") from None
    if drain_result is True:
        raise BrowserFixtureIncomplete("output_limit", "cleanup") from None


async def _settle_seed_process(
    process: asyncio.subprocess.Process,
    process_done: asyncio.Task[int],
    output_drain: asyncio.Task[bool],
) -> None:
    cleanup = asyncio.create_task(_stop_seed_process(process, process_done, output_drain))
    _, cancelled = await _retained(cleanup)
    if cancelled:
        raise asyncio.CancelledError


async def _drain_seed_output(
    reader: asyncio.StreamReader, ready_line: asyncio.Future[bytes]
) -> bool:
    first_line = bytearray()
    line_complete = False
    output_exceeded = False
    total_bytes = 0
    try:
        while chunk := await reader.read(4096):
            total_bytes += len(chunk)
            output_exceeded |= total_bytes > SEED_STDOUT_LIMIT
            if line_complete:
                continue
            prefix, separator, _suffix = chunk.partition(b"\n")
            if separator:
                prefix += separator
                line_complete = True
            remaining = SEED_STDOUT_LIMIT + 1 - len(first_line)
            first_line.extend(prefix[:remaining])
            output_exceeded |= len(first_line) > SEED_STDOUT_LIMIT
            if line_complete and not ready_line.done():
                ready_line.set_result(bytes(first_line))
        if not ready_line.done():
            ready_line.set_result(bytes(first_line))
    except Exception:
        if not ready_line.done():
            ready_line.set_result(b"")
        raise
    return output_exceeded


async def _discard_seed_output(reader: asyncio.StreamReader) -> None:
    while await reader.read(4096):
        pass


async def _spawn_seed_process(
    root: Path, ready: Path, deadline: float
) -> tuple[
    asyncio.subprocess.Process,
    asyncio.Task[int],
    asyncio.Future[bytes],
    asyncio.Task[bool],
]:
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVER_SCRIPT),
            "--seed-interrupted",
            str(root),
            str(ready),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=SEED_STDOUT_LIMIT,
        )
    )
    timed_out = False
    cancelled = False
    while not spawn.done():
        try:
            if timed_out:
                await asyncio.shield(spawn)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                done, _ = await asyncio.wait({spawn}, timeout=max(0, remaining))
                timed_out = not done
        except asyncio.CancelledError:
            cancelled = True
    try:
        process = spawn.result()
    except Exception:
        raise BrowserFixtureIncomplete("seed_dispatch_failed", "spawn") from None
    process_done = asyncio.create_task(process.wait())
    loop = asyncio.get_running_loop()
    ready_line: asyncio.Future[bytes] = loop.create_future()
    if process.stdout is None:
        output_drain = asyncio.create_task(_missing_seed_output(ready_line))
        await _settle_seed_process(process, process_done, output_drain)
        raise BrowserFixtureIncomplete("readiness_unavailable", "readiness")
    output_drain = asyncio.create_task(_drain_seed_output(process.stdout, ready_line))
    if timed_out or cancelled:
        await _settle_seed_process(process, process_done, output_drain)
        if cancelled:
            raise asyncio.CancelledError
        raise BrowserFixtureIncomplete("watchdog_exhausted", "spawn")
    return process, process_done, ready_line, output_drain


async def _missing_seed_output(ready_line: asyncio.Future[bytes]) -> bool:
    if not ready_line.done():
        ready_line.set_result(b"")
    return False


def browser_port(values: Mapping[str, str]) -> int:
    """Parse the isolated port selected by the Playwright harness."""
    raw = values.get("TETHER_BROWSER_PORT")
    if raw is None:
        raise RuntimeError("browser_fixture_port_required")
    try:
        port = int(raw)
    except ValueError as error:
        raise RuntimeError("browser_fixture_port_invalid") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("browser_fixture_port_invalid")
    return port


class FabricatedIdentity:
    async def actor_for(self, request: Request) -> Actor:
        return Actor(id=request.headers.get("x-fixture-owner", "demo"))


async def seed_interrupted(root: Path, ready: Path) -> None:
    await asyncio.to_thread(ready.write_text, "seed process entered\n")
    pause_event = asyncio.Event()
    async with await browser_host(root, pause_signal=ready, pause_event=pause_event) as host:
        await asyncio.to_thread(ready.write_text, "host opened\n")
        client = host.client(Actor(id="demo"))
        session = await client.create(title="Interrupted browser recovery", spec="demo")
        await asyncio.to_thread(ready.write_text, f"session created: {session.id}\n")
        turn = await client.send(
            session.id,
            "Recover this interrupted fabricated turn.",
            request_id="browser-recovery-seed",
            spec="demo",
        )
        await asyncio.to_thread(ready.write_text, f"turn accepted: {session.id} {turn.turn_id}\n")
        result_task = asyncio.create_task(turn.result())
        pause_task = asyncio.create_task(pause_event.wait())
        done, _pending = await asyncio.wait(
            {result_task, pause_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if result_task in done:
            pause_task.cancel()
            await asyncio.gather(pause_task, return_exceptions=True)
            result = result_task.result()
            raise RuntimeError(
                f"recovery seed turn ended before durable pause: {result.stop_reason}"
            )
        await asyncio.Future()


async def seed_interrupted_process(root: Path, *, watchdog_seconds: float) -> None:
    ready = root / ".browser-recovery-ready"
    ready.write_text("parent launched seed process\n")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + watchdog_seconds
    process, process_done, ready_line, output_drain = await _spawn_seed_process(
        root, ready, deadline
    )
    try:
        done, _ = await asyncio.wait(
            {ready_line, process_done},
            timeout=max(0, deadline - loop.time()),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready_line not in done:
            reason = (
                "watchdog_exhausted" if process_done not in done else "seed_exited_before_ready"
            )
            raise BrowserFixtureIncomplete(reason, "readiness")
        if ready_line.result() != b"BROWSER_RECOVERY_READY\n":
            raise BrowserFixtureIncomplete("seed_not_ready", "readiness")
        if process_done.done():
            raise BrowserFixtureIncomplete("seed_exited_before_stop", "readiness")
        if ready.read_text() != "model paused after provisional text\n":
            raise BrowserFixtureIncomplete("seed_state_mismatch", "readiness")
    finally:
        await _settle_seed_process(process, process_done, output_drain)
    await asyncio.to_thread(ready.unlink)
    # The public JSONL profile only recovers leases after their bounded lifetime.
    await asyncio.sleep(JSONL_LEASE_SECONDS + 1)


async def serve_browser(root: Path, *, port: int) -> None:
    async with await browser_host(root) as host:
        api = create_api(
            clients=host.client,
            authenticator=FabricatedIdentity(),
            settings=ApiSettings(api_prefix="/v1", heartbeat_seconds=0.1),
        )
        ui = create_static_app(
            settings=UiSettings(
                mount_path="/assistant",
                api_discovery_path="/assistant/api/v1/capabilities",
            )
        )

        @asynccontextmanager
        async def lifespan(_app: Starlette):
            yield

        app = Starlette(
            routes=[Mount("/assistant/api", app=api), Mount("/", app=ui)],
            lifespan=lifespan,
        )
        config = Config()
        config.bind = [f"127.0.0.1:{port}"]
        config.accesslog = None
        config.graceful_timeout = 5
        await serve(cast(ASGIFramework, app), config)


async def main(port: int, *, watchdog_seconds: float) -> None:
    with TemporaryDirectory(prefix="tether-browser-") as directory:
        root = Path(directory)
        await seed_interrupted_process(root, watchdog_seconds=watchdog_seconds)
        await serve_browser(root, port=port)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--seed-interrupted":
            asyncio.run(seed_interrupted(Path(sys.argv[2]), Path(sys.argv[3])))
        else:
            # The fixture process is the test-harness environment boundary.
            asyncio.run(
                main(
                    browser_port(os.environ),  # noqa: TID251
                    watchdog_seconds=functional_watchdog_seconds(os.environ),  # noqa: TID251
                )
            )
    except BrowserFixtureIncomplete as error:
        print(
            json.dumps({"status": "incomplete", "reason": error.reason, "phase": error.phase}),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
