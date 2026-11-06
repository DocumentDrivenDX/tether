"""Bounded tooling subprocesses with explicit, retained process ownership."""

from __future__ import annotations

import asyncio
import math
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

T = TypeVar("T")
CommandPhase = Literal["spawn", "execution", "cleanup"]
CommandReason = Literal[
    "dispatch_failed",
    "unsupported_platform",
    "watchdog_exhausted",
    "cleanup_unconfirmed",
    "output_capture_failed",
    "owned_group_not_empty",
]


class CommandIncompleteError(RuntimeError):
    """A command did not produce a complete, trustworthy child result."""

    def __init__(self, reason: CommandReason, phase: CommandPhase) -> None:
        self.reason = reason
        self.phase = phase
        super().__init__(f"tool_command_{reason}")


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


async def _retained(task: asyncio.Task[T]) -> tuple[T, bool]:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


def _signal_owned(child: asyncio.subprocess.Process, owned_group: bool) -> None:
    try:
        if owned_group:
            os.killpg(child.pid, signal.SIGKILL)
        elif child.returncode is None:
            child.kill()
    except ProcessLookupError:
        pass
    except PermissionError:
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None


async def _group_absent(pgid: int) -> None:
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None
        await asyncio.sleep(0.01)


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None
    return True


async def _wait_for_streams(
    streams: asyncio.Task[tuple[bytes | None, bytes | None]], deadline_seconds: float | None
) -> bool:
    done, _ = await asyncio.wait({streams}, timeout=deadline_seconds)
    return bool(done)


async def _cleanup(
    child: asyncio.subprocess.Process,
    streams: asyncio.Task[tuple[bytes | None, bytes | None]],
    owned_group: bool,
) -> None:
    _signal_owned(child, owned_group)
    stream_failed = False
    try:
        await streams
    except BaseException:
        stream_failed = True
    try:
        await child.wait()
        if owned_group:
            await _group_absent(child.pid)
    except CommandIncompleteError:
        raise
    except BaseException:
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None
    if stream_failed:
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup")


async def _settle(
    child: asyncio.subprocess.Process,
    streams: asyncio.Task[tuple[bytes | None, bytes | None]],
    owned_group: bool,
) -> None:
    _, cancelled = await _retained(asyncio.create_task(_cleanup(child, streams, owned_group)))
    if cancelled:
        raise asyncio.CancelledError


async def _spawn_command(
    args: Sequence[str], cwd: Path | None, stream: int | None, owned_group: bool
) -> asyncio.subprocess.Process:
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=stream,
            stderr=stream,
            start_new_session=owned_group,
        )
    )
    try:
        return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            child, _ = await _retained(spawn)
        except Exception:
            raise CommandIncompleteError("dispatch_failed", "spawn") from None
        streams = asyncio.create_task(child.communicate())
        await _settle(child, streams, owned_group)
        raise
    except OSError:
        raise CommandIncompleteError("dispatch_failed", "spawn") from None
    except Exception:
        raise CommandIncompleteError("dispatch_failed", "spawn") from None


async def _complete_command(
    child: asyncio.subprocess.Process, deadline_seconds: float | None, owned_group: bool
) -> Result:
    streams = asyncio.create_task(child.communicate())
    try:
        complete = await _wait_for_streams(streams, deadline_seconds)
    except asyncio.CancelledError:
        await _settle(child, streams, owned_group)
        raise
    except Exception:
        await _settle(child, streams, owned_group)
        raise CommandIncompleteError("cleanup_unconfirmed", "execution") from None
    if not complete:
        await _settle(child, streams, owned_group)
        raise CommandIncompleteError("watchdog_exhausted", "execution")
    try:
        out, err = streams.result()
    except BaseException:
        await _settle(child, streams, owned_group)
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup") from None
    if child.returncode is None:
        await _settle(child, streams, owned_group)
        raise CommandIncompleteError("cleanup_unconfirmed", "cleanup")
    if owned_group:
        try:
            group_exists = _group_exists(child.pid)
        except CommandIncompleteError:
            await _settle(child, streams, owned_group)
            raise
        if group_exists:
            await _settle(child, streams, True)
            raise CommandIncompleteError("owned_group_not_empty", "cleanup")
    return Result(
        child.returncode,
        (out or b"").decode(errors="replace"),
        (err or b"").decode(errors="replace"),
    )


async def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    deadline_seconds: float | None = 180,
    own_process_group: bool = False,
) -> Result:
    if not args or not Path(args[0]).is_absolute():
        raise ValueError("tooling requires an absolute executable")
    if deadline_seconds is not None and (
        not math.isfinite(deadline_seconds) or deadline_seconds <= 0
    ):
        raise ValueError("tooling deadline must be finite and positive")
    stream = asyncio.subprocess.PIPE if capture else None
    child = await _spawn_command(args, cwd, stream, own_process_group)
    return await _complete_command(child, deadline_seconds, own_process_group)
