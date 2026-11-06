"""Pinned, allowlisted subprocess environment for benchmark children."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from selected_profiles_process import await_retained

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
_MAX_CHILD_CAPTURE_BYTES = 1024 * 1024


def child_environment(
    parent: Mapping[str, str] | None = None, overrides: dict[str, str] | None = None
) -> dict[str, str]:
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP", "TZ")
    environment = {key: parent[key] for key in allowed if parent is not None and key in parent}
    environment.setdefault("PATH", os.defpath)
    environment.setdefault("HOME", str(Path.home()))
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if overrides:
        if set(overrides) - {"TERM"}:
            raise ValueError("benchmark_child_environment_override_invalid")
        environment.update(overrides)
    return environment


def find_verified_tether_executable(environment: Mapping[str, str]) -> Path:
    executable_text = shutil.which("tether", path=environment["PATH"])
    if executable_text is None:
        raise RuntimeError("benchmark_tether_executable_unavailable")
    executable = Path(executable_text).absolute()
    try:
        with executable.open("rb") as stream:
            shebang = stream.readline(4097).rstrip(b"\r\n")
    except OSError:
        raise RuntimeError("benchmark_tether_executable_unreadable") from None
    expected = f"#!{Path(sys.executable).absolute()}".encode()
    if len(shebang) > 4096 or shebang != expected:
        raise RuntimeError("benchmark_tether_interpreter_mismatch")
    return executable


async def verified_tether_executable(environment: Mapping[str, str]) -> Path:
    executable = await asyncio.to_thread(find_verified_tether_executable, environment)
    code = "import tether; from pathlib import Path; print(Path(tether.__file__).resolve())"
    returncode, stdout, _ = await run_clean_capture(
        (sys.executable, "-c", code), environment=environment, timeout_seconds=30
    )
    expected = str((SOURCE_ROOT / "tether" / "__init__.py").resolve())
    if returncode != 0 or stdout.decode("utf-8", errors="replace").strip() != expected:
        raise RuntimeError("benchmark_tether_source_import_mismatch")
    return executable


async def run_clean_capture(
    command: tuple[str, ...], *, environment: Mapping[str, str], timeout_seconds: float
) -> tuple[int, bytes, bytes]:
    owner = asyncio.create_task(_capture(command, environment, timeout_seconds))
    return await await_retained(owner)


async def _capture(
    command: tuple[str, ...], environment: Mapping[str, str], timeout_seconds: float
) -> tuple[int, bytes, bytes]:
    process: asyncio.subprocess.Process | None = None
    readers: list[asyncio.Task[bytes]] = []
    try:
        async with asyncio.timeout(timeout_seconds):
            process = await _spawn_owned(
                command,
                environment,
            )
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("benchmark_child_pipe_unavailable")
            readers = [
                asyncio.create_task(_read_bounded(process.stdout)),
                asyncio.create_task(_read_bounded(process.stderr)),
            ]
            stdout, stderr = await asyncio.gather(*readers)
            await process.wait()
        if process.returncode is None:
            raise RuntimeError("benchmark_child_exit_unobserved")
        return process.returncode, stdout, stderr
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        if process is not None:
            await _stop_process(process)


async def _spawn_owned(
    command: tuple[str, ...],
    environment: Mapping[str, str],
    *,
    stderr: int = asyncio.subprocess.PIPE,
) -> asyncio.subprocess.Process:
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            cwd=PROJECT_ROOT,
            env=dict(environment),
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
        )
    )
    try:
        return await asyncio.shield(spawn)
    except BaseException:
        process: asyncio.subprocess.Process | None = None
        with suppress(BaseException):
            process = await await_retained(spawn)
        if process is not None:
            await await_retained(asyncio.create_task(_stop_process(process)))
        raise


async def _read_bounded(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await stream.read(4096):
        output.extend(chunk)
        if len(output) > _MAX_CHILD_CAPTURE_BYTES:
            raise RuntimeError("benchmark_child_output_limit")
    return bytes(output)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()
