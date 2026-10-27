"""Prove the installed offline UI paints and exits under a real POSIX terminal."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import select
import struct
import sys
import tempfile
import termios
import time
from collections.abc import Callable
from pathlib import Path

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


def read_until(master: int, poll: Callable[[], int | None], marker: bytes, seconds: float) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + seconds
    while marker not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], max(0.0, deadline - time.monotonic()))
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 1024 * 1024:
                raise ValueError("terminal output exceeded 1 MiB")
        elif poll() is not None:
            break
    return bytes(output)


async def terminal_journey(root: Path) -> None:
    master, slave = pty.openpty()
    child: asyncio.subprocess.Process | None = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
        child = await asyncio.create_subprocess_exec(
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
        os.close(slave)
        slave = -1
        painted = await asyncio.to_thread(
            read_until, master, lambda: child.returncode, b"Offline demo", 10
        )
        if b"Offline demo" not in painted:
            raise ValueError("installed terminal UI did not paint")
        if child.returncode is not None:
            raise ValueError("installed terminal UI exited before input")
        os.write(master, b"\x11")  # Textual's Ctrl+Q quit binding.
        await asyncio.to_thread(read_until, master, lambda: child.returncode, b"\xff", 5)
        if await asyncio.wait_for(child.wait(), 5) != 0:
            raise ValueError("terminal UI did not exit cleanly")
        if not (root / "journal.jsonl").is_file():
            raise ValueError("local demo journal missing")
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        if slave >= 0:
            os.close(slave)
        os.close(master)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="installed-terminal-") as directory:
        asyncio.run(terminal_journey(Path(directory)))
    print("offline_installed_terminal_pty=True")


if __name__ == "__main__":
    main()
