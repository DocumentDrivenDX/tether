"""Isolated asyncpg startup-authentication oracle for selected-profile qualification."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

import asyncpg

MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 4 * 1024
CHILD_TIMEOUT_SECONDS = 25
CONNECT_TIMEOUT_SECONDS = 18
QUERY_TIMEOUT_SECONDS = 4
CLOSE_TIMEOUT_SECONDS = 2
AUTH_SQLSTATES = frozenset(("28000", "28P01"))
EXPECTED_ASYNCPG_VERSION = "0.32.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Connection(Protocol):
    async def fetchval(self, query: str) -> object: ...

    async def close(self) -> None: ...


class _AsyncPG(Protocol):
    async def connect(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        ssl: ssl.SSLContext,
    ) -> _Connection: ...


@dataclass(frozen=True)
class OracleResult:
    kind: Literal["connected", "auth_refused", "other_failure"]
    current_user: str | None = None
    sqlstate: str | None = None
    driver_version: str = EXPECTED_ASYNCPG_VERSION


def _auth_sqlstate(error: BaseException) -> str | None:
    if not isinstance(error, asyncpg.PostgresError):
        return None
    code = getattr(error, "sqlstate", None)
    return code if isinstance(code, str) and code in AUTH_SQLSTATES else None


def _connection_arguments(value: object) -> tuple[str, int, str, str, str, str]:
    if not isinstance(value, dict) or set(value) != {
        "host",
        "port",
        "user",
        "password",
        "database",
        "ca_file",
    }:
        raise ValueError("invalid_oracle_request")
    host, port, user, password, database, ca_file = (
        value[key] for key in ("host", "port", "user", "password", "database", "ca_file")
    )
    if (
        not isinstance(host, str)
        or not host
        or len(host) > 1024
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(user, str)
        or not user
        or len(user) > 512
        or not isinstance(password, str)
        or not password
        or len(password) > 16 * 1024
        or not isinstance(database, str)
        or not database
        or len(database) > 512
        or not isinstance(ca_file, str)
        or not Path(ca_file).is_absolute()
        or len(ca_file) > 4096
    ):
        raise ValueError("invalid_oracle_request")
    return host, port, user, password, database, ca_file


async def _run_connection(value: object) -> OracleResult:
    driver_version = getattr(asyncpg, "__version__", "")
    if driver_version != EXPECTED_ASYNCPG_VERSION:
        return OracleResult("other_failure", driver_version=str(driver_version))
    host, port, user, password, database, ca_file = _connection_arguments(value)
    context = ssl.create_default_context(cafile=ca_file)
    context.check_hostname = True
    connection: _Connection | None = None
    try:
        async with asyncio.timeout(CHILD_TIMEOUT_SECONDS - 2):
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                connection = await cast(_AsyncPG, asyncpg).connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    ssl=context,
                )
            current_user = await asyncio.wait_for(
                connection.fetchval("SELECT current_user"), timeout=QUERY_TIMEOUT_SECONDS
            )
            if not isinstance(current_user, str):
                return OracleResult("other_failure", driver_version=driver_version)
            return OracleResult(
                "connected", current_user=current_user, driver_version=driver_version
            )
    except Exception as error:
        sqlstate = _auth_sqlstate(error)
        if sqlstate is not None:
            return OracleResult("auth_refused", sqlstate=sqlstate, driver_version=driver_version)
        return OracleResult("other_failure", driver_version=driver_version)
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(connection.close(), timeout=CLOSE_TIMEOUT_SECONDS)


def _encode_result(result: OracleResult) -> bytes:
    data = json.dumps(
        {
            "kind": result.kind,
            "current_user": result.current_user,
            "sqlstate": result.sqlstate,
            "driver_version": result.driver_version,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > MAX_OUTPUT_BYTES:
        raise RuntimeError("oracle_output_limit")
    return data + b"\n"


async def _child_main(raw: bytes) -> int:
    if len(raw) > MAX_INPUT_BYTES:
        result = OracleResult(
            "other_failure", driver_version=str(getattr(asyncpg, "__version__", ""))
        )
    else:
        try:
            request = json.loads(raw)
            result = await _run_connection(request)
        except Exception:
            result = OracleResult(
                "other_failure", driver_version=str(getattr(asyncpg, "__version__", ""))
            )
    sys.stdout.buffer.write(_encode_result(result))
    sys.stdout.buffer.flush()
    return 0


def _child_environment() -> dict[str, str]:
    executable_dir = str(Path(sys.executable).absolute().parent)
    return {
        "PATH": os.pathsep.join((executable_dir, "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def _read_child_output(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await stream.read(1024):
        if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
            raise RuntimeError("database_oracle_output_limit")
        output.extend(chunk)
    return bytes(output)


async def _kill_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


async def _cleanup_child(
    process: asyncio.subprocess.Process, reader: asyncio.Task[bytes] | None
) -> None:
    if process.returncode is None:
        await _kill_child(process)
    if reader is not None:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def _await_cleanup(task: asyncio.Task[None]) -> None:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


def _decode_output(raw: bytes, returncode: int) -> OracleResult:
    if returncode != 0 or not raw or len(raw) > MAX_OUTPUT_BYTES or not raw.endswith(b"\n"):
        raise RuntimeError("database_oracle_child_failed")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("database_oracle_child_failed") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "current_user", "sqlstate", "driver_version"}
        or value.get("kind") not in {"connected", "auth_refused", "other_failure"}
        or (
            value.get("current_user") is not None and not isinstance(value.get("current_user"), str)
        )
        or (value.get("sqlstate") is not None and not isinstance(value.get("sqlstate"), str))
        or value.get("driver_version") != EXPECTED_ASYNCPG_VERSION
    ):
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "connected" and value["sqlstate"] is not None:
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "auth_refused" and value["sqlstate"] not in AUTH_SQLSTATES:
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "other_failure" and (
        value["sqlstate"] is not None or value["current_user"] is not None
    ):
        raise RuntimeError("database_oracle_child_failed")
    return OracleResult(**value)


async def probe_database(
    *, host: str, port: int, user: str, password: str, database: str, ca_file: str
) -> OracleResult:
    """Run one explicit-TLS authentication attempt in a bounded child process."""
    if getattr(asyncpg, "__version__", None) != EXPECTED_ASYNCPG_VERSION:
        raise RuntimeError("database_oracle_driver_version_mismatch")
    request = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "ca_file": ca_file,
    }
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_INPUT_BYTES:
        raise RuntimeError("database_oracle_input_limit")
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task[bytes] | None = None
    try:
        async with asyncio.timeout(CHILD_TIMEOUT_SECONDS):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "scripts.selected_profiles_pg_oracle",
                cwd=PROJECT_ROOT,
                env=_child_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("database_oracle_pipe_unavailable")
            reader = asyncio.create_task(_read_child_output(process.stdout))
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            await process.wait()
            raw = await reader
            reader = None
            returncode = process.returncode
            if returncode is None:
                raise RuntimeError("database_oracle_child_failed")
            return _decode_output(raw, returncode)
    except TimeoutError:
        raise RuntimeError("database_oracle_timeout") from None
    finally:
        if process is not None:
            cleanup = asyncio.create_task(_cleanup_child(process, reader))
            await _await_cleanup(cleanup)


async def require_authentication_refusal(
    *, host: str, port: int, user: str, password: str, database: str, ca_file: str
) -> tuple[str, str]:
    result = await probe_database(
        host=host, port=port, user=user, password=password, database=database, ca_file=ca_file
    )
    if result.kind != "auth_refused" or result.sqlstate not in AUTH_SQLSTATES:
        raise RuntimeError("database_auth_refusal_unclassified")
    return result.sqlstate, result.driver_version


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_child_main(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))))


__all__ = ["OracleResult", "probe_database", "require_authentication_refusal"]
