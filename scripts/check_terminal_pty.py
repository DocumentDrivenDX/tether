"""Prove the installed offline UI paints and exits under a real POSIX terminal."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import sys
import tempfile
import termios
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

CHILD = """
import socket
import sys
from pathlib import Path

def deny_network(*args, **kwargs):
    raise AssertionError('terminal demo attempted network I/O')

socket.socket.connect = deny_network
socket.create_connection = deny_network
import tether
assert Path(tether.__file__).is_relative_to(Path(sys.prefix))
from tether.cli.command import main
raise SystemExit(main(['--root', sys.argv[1]]))
"""
OPERATOR_WATCHDOG_SECONDS = 30 * 60
OUTPUT_LIMIT = 1024 * 1024


class ProbeIncompleteError(RuntimeError):
    """The terminal check could not establish a complete owned result."""

    def __init__(self, reason: str, phase: str) -> None:
        self.reason = reason
        self.phase = phase
        super().__init__(f"terminal_probe_{reason}")


def append_output(output: bytearray, chunk: bytes) -> None:
    output.extend(chunk)
    if len(output) > OUTPUT_LIMIT:
        raise ProbeIncompleteError("output_limit", "capture")


def _drain_pty(master: int, output: bytearray, marker: bytes | None) -> bool:
    while True:
        try:
            chunk = os.read(master, 65536)
        except BlockingIOError:
            return False
        except OSError:
            return True
        if not chunk:
            return True
        append_output(output, chunk)
        if marker is not None and marker in output:
            return True


async def _wait_for_pty_or_exit(
    ready: asyncio.Event, child_done: asyncio.Future[int], deadline: float
) -> None:
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise TimeoutError("terminal PTY operator watchdog expired")
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {ready_task, child_done},
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError("terminal PTY operator watchdog expired")
    finally:
        if not ready_task.done():
            ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)


async def _retained(task: asyncio.Task[T]) -> tuple[T, bool]:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def read_until(
    master: int,
    child_done: asyncio.Future[int],
    marker: bytes | None,
    deadline: float,
) -> bytes:
    output = bytearray()
    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    os.set_blocking(master, False)
    loop.add_reader(master, ready.set)
    try:
        while True:
            if _drain_pty(master, output, marker):
                return bytes(output)
            if child_done.done():
                return bytes(output)
            await _wait_for_pty_or_exit(ready, child_done, deadline)
            ready.clear()
    finally:
        loop.remove_reader(master)


def _signal_owned_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise ProbeIncompleteError("cleanup_unconfirmed", "cleanup") from None


async def _wait_for_owned_group_exit(pgid: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + OPERATOR_WATCHDOG_SECONDS
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            raise ProbeIncompleteError("cleanup_unconfirmed", "cleanup") from None
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ProbeIncompleteError("watchdog_exhausted", "cleanup") from None
        await asyncio.sleep(min(0.05, remaining))


async def _cleanup_child(child: asyncio.subprocess.Process, child_done: asyncio.Task[int]) -> None:
    _signal_owned_group(child.pid)
    await child_done
    await _wait_for_owned_group_exit(child.pid)


async def _settle_child(child: asyncio.subprocess.Process, child_done: asyncio.Task[int]) -> None:
    _, cancelled = await _retained(asyncio.create_task(_cleanup_child(child, child_done)))
    if cancelled:
        raise asyncio.CancelledError


async def _await_child_done(child_done: asyncio.Task[int], deadline: float) -> int:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("terminal PTY operator watchdog expired")
    return await asyncio.wait_for(asyncio.shield(child_done), timeout=remaining)


async def _spawn_pty_child(
    root: Path, slave: int
) -> tuple[asyncio.subprocess.Process, asyncio.Task[int]]:
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            CHILD,
            str(root),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            env={"TERM": "xterm-256color", "LANG": "C.UTF-8"},
        )
    )
    cancelled = False
    while not spawn.done():
        try:
            await asyncio.shield(spawn)
        except asyncio.CancelledError:
            cancelled = True
    try:
        child = spawn.result()
    except Exception:
        raise ProbeIncompleteError("dispatch_failed", "spawn") from None
    child_done = asyncio.create_task(child.wait())
    if cancelled:
        await _settle_child(child, child_done)
        raise asyncio.CancelledError
    return child, child_done


async def _exercise_terminal(
    root: Path, master: int, child_done: asyncio.Task[int], deadline: float
) -> None:
    painted = await read_until(master, child_done, b"Send a fabricated message", deadline)
    if b"Send a fabricated message" not in painted:
        raise ValueError("installed terminal UI did not paint")
    if child_done.done():
        raise ValueError("installed terminal UI exited before input")
    os.write(master, b"Fabricated request\r")
    reply = await read_until(master, child_done, b"Approval requested", deadline)
    if b"Approval requested" not in reply:
        raise ValueError("installed terminal UI did not show first response")
    os.write(master, b"\x11")  # Textual's Ctrl+Q quit binding.
    await read_until(master, child_done, None, deadline)
    exit_code = await _await_child_done(child_done, deadline)
    if exit_code != 0:
        raise ValueError(f"terminal UI exited {exit_code}")
    if not (root / "journal.jsonl").is_file():
        raise ValueError("local demo journal missing")


async def terminal_journey(root: Path) -> None:
    master, slave = pty.openpty()
    child: asyncio.subprocess.Process | None = None
    child_done: asyncio.Task[int] | None = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
        deadline = asyncio.get_running_loop().time() + OPERATOR_WATCHDOG_SECONDS
        child, child_done = await _spawn_pty_child(root, slave)
        os.close(slave)
        slave = -1
        await _exercise_terminal(root, master, child_done, deadline)
    finally:
        try:
            if child is not None and child_done is not None:
                await _settle_child(child, child_done)
        finally:
            if slave >= 0:
                os.close(slave)
            os.close(master)


def main() -> None:
    try:
        with tempfile.TemporaryDirectory(prefix="installed-terminal-") as directory:
            asyncio.run(terminal_journey(Path(directory)))
    except ProbeIncompleteError as error:
        _report_incomplete(error)
    except TimeoutError:
        _report_incomplete(ProbeIncompleteError("watchdog_exhausted", "execution"))
    print(json.dumps(dict(offline_installed_terminal_pty=True)))


def _report_incomplete(error: ProbeIncompleteError) -> None:
    print(
        json.dumps(
            {
                "offline_installed_terminal_pty": False,
                "status": "incomplete",
                "reason": error.reason,
                "phase": error.phase,
            }
        ),
        file=sys.stderr,
    )
    raise SystemExit(2) from None


if __name__ == "__main__":
    main()
