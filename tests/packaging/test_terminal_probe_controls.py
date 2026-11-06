"""Real POSIX terminal children exercise probe I/O and deterministic refusal.

The child is a tiny fabricated terminal program, not the Tether installed demo;
the separate installed matrix remains responsible for that product journey.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

import check_terminal_pty as probe
import pytest

CHILD = """
import os,sys,tty
from pathlib import Path
tty.setraw(sys.stdin.fileno())
mode = MODE
if mode == 'no-paint':
    raise SystemExit(0)
os.write(sys.stdout.fileno(), b'Send a fabricated message')
request = bytearray()
while not request.endswith(b'\\r'):
    request.extend(os.read(sys.stdin.fileno(), 1))
if mode == 'no-reply':
    raise SystemExit(0)
os.write(sys.stdout.fileno(), b'Approval requested')
while os.read(sys.stdin.fileno(), 1) != b'\\x11':
    pass
if mode == 'nonzero':
    raise SystemExit(7)
if mode != 'no-journal':
    Path(sys.argv[1], 'journal.jsonl').write_text('fabricated journal')
"""


@pytest.mark.parametrize("mode", ["success", "no-paint", "no-reply", "nonzero", "no-journal"])
def test_terminal_probe_requires_paint_reply_clean_exit_and_journal(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(probe, "CHILD", CHILD.replace("MODE", repr(mode)))
    expected = {
        "no-paint": "did not paint",
        "no-reply": "did not show first response",
        "nonzero": "terminal UI exited 7",
        "no-journal": "journal missing",
    }
    pending = probe.terminal_journey(tmp_path)
    if mode == "success":
        asyncio.run(pending)
    else:
        with pytest.raises(ValueError, match=expected[mode]):
            asyncio.run(pending)


def test_read_until_returns_eof_without_inventing_marker():
    reader, writer = os.pipe()
    try:
        os.write(writer, b"fabricated partial frame")
        os.close(writer)
        writer = -1

        async def read_eof():
            loop = asyncio.get_running_loop()
            done = loop.create_future()
            done.set_result(0)
            return await probe.read_until(reader, done, b"absent-marker", loop.time() + 10)

        observed = asyncio.run(read_eof())
        assert observed == b"fabricated partial frame"
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)


def test_output_accumulator_refuses_unbounded_output():
    with pytest.raises(probe.ProbeIncompleteError, match="terminal_probe_output_limit"):
        probe.append_output(bytearray(), b"x" * (probe.OUTPUT_LIMIT + 1))


def _assert_group_absent(pgid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)


def test_spawn_cancellation_retains_and_cleans_the_new_process_group(tmp_path, monkeypatch):
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        spawned: list[int] = []
        original_spawn = asyncio.create_subprocess_exec

        async def held_spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            child = await original_spawn(*args, **kwargs)
            spawned.append(child.pid)
            return child

        monkeypatch.setattr(probe, "CHILD", "import time; time.sleep(60)")
        monkeypatch.setattr(probe.asyncio, "create_subprocess_exec", held_spawn)
        task = asyncio.create_task(probe.terminal_journey(tmp_path))
        try:
            await entered.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(spawned) == 1
            _assert_group_absent(spawned[0])
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(exercise())


def test_repeated_cancellation_retains_owned_group_cleanup(tmp_path, monkeypatch):
    async def exercise():
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            cwd=tmp_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        child_done = asyncio.create_task(child.wait())
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        original_wait = probe._wait_for_owned_group_exit

        async def held_cleanup(pgid: int) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await original_wait(pgid)

        monkeypatch.setattr(probe, "_wait_for_owned_group_exit", held_cleanup)
        cleanup = asyncio.create_task(probe._settle_child(child, child_done))
        try:
            await cleanup_started.wait()
            cleanup.cancel()
            cleanup.cancel()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await cleanup
            _assert_group_absent(child.pid)
        finally:
            release_cleanup.set()
            if not cleanup.done():
                cleanup.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cleanup
            if child.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                await child.wait()
            _assert_group_absent(child.pid)

    asyncio.run(exercise())


def test_terminal_probe_main_reports_success_without_timing_fields(monkeypatch, capsys):
    async def journey(root):
        assert root.is_absolute()

    monkeypatch.setattr(probe, "terminal_journey", journey)
    probe.main()
    assert '"offline_installed_terminal_pty": true' in capsys.readouterr().out
    assert sys.platform != "win32"
