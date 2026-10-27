"""Prove the installed offline UI paints and exits under a real POSIX terminal."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
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


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="installed-terminal-") as directory:
        root = Path(directory)
        master, slave = pty.openpty()
        child_pid: int | None = None
        exit_code: int | None = None

        def poll() -> int | None:
            nonlocal exit_code
            if child_pid is not None and exit_code is None:
                completed, status = os.waitpid(child_pid, os.WNOHANG)
                if completed:
                    exit_code = os.waitstatus_to_exitcode(status)
            return exit_code

        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
            child_pid = os.posix_spawn(
                sys.executable,
                [sys.executable, "-I", "-c", CHILD, str(root)],
                {"TERM": "xterm-256color", "LANG": "C.UTF-8"},
                file_actions=[
                    (os.POSIX_SPAWN_DUP2, slave, 0),
                    (os.POSIX_SPAWN_DUP2, slave, 1),
                    (os.POSIX_SPAWN_DUP2, slave, 2),
                ],
                setsid=True,
            )
            os.close(slave)
            slave = -1
            painted = read_until(master, poll, b"Offline demo", 10)
            if b"Offline demo" not in painted:
                raise ValueError("installed terminal UI did not paint")
            if poll() is not None:
                raise ValueError("installed terminal UI exited before input")
            os.write(master, b"\x11")  # Textual's Ctrl+Q quit binding.
            read_until(master, poll, b"\xff", 5)  # Drain until the child closes the terminal.
            if poll() != 0:
                raise ValueError("terminal UI did not exit cleanly")
            if not (root / "journal.jsonl").is_file():
                raise ValueError("local demo journal missing")
        finally:
            if child_pid is not None and poll() is None:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
            if slave >= 0:
                os.close(slave)
            os.close(master)
    print("offline_installed_terminal_pty=True")


if __name__ == "__main__":
    main()
