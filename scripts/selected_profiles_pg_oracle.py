"""Isolated asyncpg startup-authentication oracle for selected-profile qualification."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import asyncpg

if __package__:
    from .selected_profiles_pg_oracle_protocol import decode_result, encode_result
else:
    from selected_profiles_pg_oracle_protocol import decode_result, encode_result

if __package__:
    from .selected_profiles_process import wait_for_process_group_exit
else:
    from selected_profiles_process import wait_for_process_group_exit

MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 4 * 1024
CHILD_TIMEOUT_SECONDS = 25
CONNECT_TIMEOUT_SECONDS = 18
AUTH_SQLSTATES = frozenset(("28000", "28P01"))
EXPECTED_ASYNCPG_VERSION = "0.32.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_OBSERVATION_EXPIRED_EXIT_CODE = 75


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


class _OracleObservationExpired(Exception):
    """The bounded connection/query observation ended without a classified result."""


class _OracleParentObservationExpired(Exception):
    """The caller-owned child observation bound expired."""


@dataclass
class _OracleChild:
    spawn: asyncio.Task[asyncio.subprocess.Process]
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task[Any] | None = None


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
    result = OracleResult("other_failure", driver_version=driver_version)
    expired = False
    timeout_error: BaseException | None = None
    operation_timeout = asyncio.timeout(CHILD_TIMEOUT_SECONDS - 2)
    try:
        async with operation_timeout:
            connection = await _connect_database(host, port, user, password, database, context)
            result = await _query_database(connection, driver_version)
    except _OracleObservationExpired as error:
        expired = True
        timeout_error = error
    except TimeoutError as error:
        if operation_timeout.expired():
            expired = True
            timeout_error = error
        else:
            result = _classify_database_error(error, driver_version)
    except Exception as error:
        result = _classify_database_error(error, driver_version)
    finally:
        if connection is not None:
            close_task = asyncio.create_task(connection.close())
            try:
                await _await_cleanup(close_task)
            except Exception:
                result = OracleResult("other_failure", driver_version=driver_version)
    if expired:
        raise _OracleObservationExpired from timeout_error
    return result


async def _connect_database(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    context: ssl.SSLContext,
) -> _Connection:
    connect_timeout = asyncio.timeout(CONNECT_TIMEOUT_SECONDS)
    try:
        async with connect_timeout:
            return await cast(_AsyncPG, asyncpg).connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                ssl=context,
            )
    except TimeoutError as error:
        if connect_timeout.expired():
            raise _OracleObservationExpired from error
        raise


async def _query_database(connection: _Connection, driver_version: str) -> OracleResult:
    try:
        current_user = await connection.fetchval("SELECT current_user")
    except Exception as error:
        return _classify_database_error(error, driver_version)
    if not isinstance(current_user, str):
        return OracleResult("other_failure", driver_version=driver_version)
    return OracleResult("connected", current_user=current_user, driver_version=driver_version)


def _classify_database_error(error: BaseException, driver_version: str) -> OracleResult:
    sqlstate = _auth_sqlstate(error)
    if sqlstate is not None:
        return OracleResult("auth_refused", sqlstate=sqlstate, driver_version=driver_version)
    return OracleResult("other_failure", driver_version=driver_version)


def _encode_result(result: OracleResult) -> bytes:
    return encode_result(
        result.kind,
        result.current_user,
        result.sqlstate,
        result.driver_version,
        MAX_OUTPUT_BYTES,
    )


async def _child_main(raw: bytes) -> int:
    exit_code = 0
    if len(raw) > MAX_INPUT_BYTES:
        result = OracleResult(
            "other_failure", driver_version=str(getattr(asyncpg, "__version__", ""))
        )
    else:
        try:
            request = json.loads(raw)
            result = await _run_connection(request)
        except _OracleObservationExpired:
            result = OracleResult(
                "other_failure", driver_version=str(getattr(asyncpg, "__version__", ""))
            )
            exit_code = _OBSERVATION_EXPIRED_EXIT_CODE
        except Exception:
            result = OracleResult(
                "other_failure", driver_version=str(getattr(asyncpg, "__version__", ""))
            )
    sys.stdout.buffer.write(_encode_result(result))
    sys.stdout.buffer.flush()
    return exit_code


def _child_environment() -> dict[str, str]:
    executable_dir = str(Path(sys.executable).absolute().parent)
    return {
        "PATH": os.pathsep.join((executable_dir, "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def _read_child_output(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    overflow = False
    while chunk := await stream.read(1024):
        remaining = MAX_OUTPUT_BYTES - len(output)
        output.extend(chunk[:remaining])
        overflow = overflow or len(chunk) > remaining
    if overflow:
        raise RuntimeError("database_oracle_output_limit")
    return bytes(output)


async def _kill_child(process: asyncio.subprocess.Process) -> None:
    errors = _signal_oracle_child(process)
    waits: list[asyncio.Task[Any]] = [asyncio.create_task(process.wait())]
    if isinstance(process, asyncio.subprocess.Process) and os.name == "posix":
        waits.append(
            asyncio.create_task(asyncio.to_thread(wait_for_process_group_exit, process.pid))
        )
    outcomes = await asyncio.gather(*waits, return_exceptions=True)
    errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("database_oracle_child_cleanup_failed", errors)


def _signal_oracle_child(process: asyncio.subprocess.Process) -> list[BaseException]:
    try:
        if isinstance(process, asyncio.subprocess.Process) and os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            return _signal_direct_child(process)
    except ProcessLookupError:
        return []
    except BaseException as error:
        return [error]
    return []


def _signal_direct_child(process: asyncio.subprocess.Process) -> list[BaseException]:
    if process.returncode is not None:
        return []
    killer = getattr(process, "kill", None)
    if not callable(killer):
        return []
    try:
        killer()
    except ProcessLookupError:
        return []
    except BaseException as error:
        return [error]
    return []


async def _cleanup_child(
    process: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
) -> None:
    errors: list[BaseException] = []
    if process.stdin is not None:
        try:
            process.stdin.close()
        except BaseException as error:
            errors.append(error)
    if reader is None and process.stdout is not None:
        reader = asyncio.create_task(_drain_child_output(process.stdout))
    elif (
        reader is not None
        and reader.done()
        and (reader.cancelled() or reader.exception() is not None)
        and process.stdout is not None
    ):
        try:
            reader.result()
        except BaseException as error:
            errors.append(error)
        reader = asyncio.create_task(_drain_child_output(process.stdout))
    try:
        await _kill_child(process)
    except BaseException as error:
        errors.append(error)
    if reader is not None:
        try:
            await reader
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("database_oracle_child_cleanup_failed", errors)


async def _drain_child_output(stream: asyncio.StreamReader) -> None:
    while await stream.read(1024):
        continue


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
    wire = decode_result(
        raw,
        returncode,
        max_output_bytes=MAX_OUTPUT_BYTES,
        expected_driver_version=EXPECTED_ASYNCPG_VERSION,
        auth_sqlstates=AUTH_SQLSTATES,
        expired_exit_code=_OBSERVATION_EXPIRED_EXIT_CODE,
    )
    if returncode == _OBSERVATION_EXPIRED_EXIT_CODE:
        _raise_database_observation_incomplete()
    return OracleResult(**wire)


def _raise_database_observation_incomplete() -> None:
    from qualify_live_apps_incomplete import LiveQualificationIncomplete

    raise LiveQualificationIncomplete("qualification_database_credential_not_observed")


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
    child = _OracleChild(
        asyncio.create_task(
            asyncio.create_subprocess_exec(
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
        )
    )
    try:
        result = await _observe_oracle_child(child, payload)
    except BaseException as error:
        cleanup_error = await _finish_oracle_child(child)
        if isinstance(error, _OracleParentObservationExpired):
            if cleanup_error is not None:
                cause = BaseExceptionGroup(
                    "database_oracle_observation_cleanup_failed", [error, cleanup_error]
                )
                raise _database_observation_error(cause) from error
            _raise_database_observation_incomplete()
        if cleanup_error is not None:
            raise error from cleanup_error
        raise
    cleanup_error = await _finish_oracle_child(child)
    if isinstance(cleanup_error, asyncio.CancelledError):
        raise cleanup_error
    if cleanup_error is not None:
        raise _database_observation_error(cleanup_error)
    return result


async def _observe_oracle_child(child: _OracleChild, payload: bytes) -> OracleResult:
    timeout = asyncio.timeout(CHILD_TIMEOUT_SECONDS)
    try:
        async with timeout:
            process = await asyncio.shield(child.spawn)
            child.process = process
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("database_oracle_pipe_unavailable")
            child.reader = asyncio.create_task(_read_child_output(process.stdout))
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            await process.wait()
            reader = child.reader
            if reader is None:
                raise RuntimeError("database_oracle_reader_unavailable")
            raw = await asyncio.shield(reader)
            child.reader = None
            if process.returncode is None:
                raise RuntimeError("database_oracle_child_failed")
            return _decode_output(raw, process.returncode)
    except TimeoutError as error:
        if timeout.expired():
            raise _OracleParentObservationExpired from error
        raise


async def _finish_oracle_child(child: _OracleChild) -> BaseException | None:
    cleanup = asyncio.create_task(_cleanup_oracle_acquisition(child))
    try:
        await _await_cleanup(cleanup)
    except BaseException as error:
        return error
    return None


async def _cleanup_oracle_acquisition(child: _OracleChild) -> None:
    process = child.process
    if process is None:
        try:
            process = await asyncio.shield(child.spawn)
        except BaseException:
            return
        child.process = process
    await _cleanup_child(process, child.reader)


def _database_observation_error(cause: BaseException) -> BaseException:
    from qualify_live_apps_incomplete import LiveQualificationIncomplete

    incomplete = LiveQualificationIncomplete("qualification_database_credential_not_observed")
    incomplete.__cause__ = cause
    return incomplete


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
