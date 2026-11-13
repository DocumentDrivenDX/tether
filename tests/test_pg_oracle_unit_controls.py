"""Hermetic unit controls for the isolated asyncpg authentication oracle (fabricated, no server)."""

from __future__ import annotations

import asyncio
import io
import json
import ssl
import sys
from types import SimpleNamespace
from typing import Any, cast

import asyncpg
import pytest
import selected_profiles_pg_oracle as oracle
from pg_oracle_unit_support import _REQUEST, _VERSION, _report
from qualify_live_apps_incomplete import LiveQualificationIncomplete


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (asyncpg.InvalidPasswordError("x"), "28P01"),
        (asyncpg.InvalidAuthorizationSpecificationError("x"), "28000"),
        (asyncpg.UndefinedTableError("x"), None),
        (OSError("x"), None),
        (TimeoutError("x"), None),
    ],
)
def test_auth_sqlstate_classifies_only_authentication_refusals(
    error: BaseException, expected: str | None
) -> None:
    assert oracle._auth_sqlstate(error) == expected


def test_connection_arguments_accept_exact_request() -> None:
    assert oracle._connection_arguments(_REQUEST) == tuple(_REQUEST.values())


@pytest.mark.parametrize(
    "mutation",
    [
        {"host": ""},
        {"host": "h" * 1025},
        {"port": 0},
        {"port": 65536},
        {"port": True},
        {"port": "5432"},
        {"user": ""},
        {"user": "u" * 513},
        {"password": ""},
        {"password": "p" * (16 * 1024 + 1)},
        {"database": ""},
        {"ca_file": "relative.pem"},
        {"ca_file": 5},
        {"extra": "x"},
    ],
)
def test_connection_arguments_refuse_invalid_requests(mutation: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="invalid_oracle_request"):
        oracle._connection_arguments({**_REQUEST, **mutation})


def test_connection_arguments_refuse_non_mapping_and_missing_keys() -> None:
    for bad in ([], {"host": "h"}):
        with pytest.raises(ValueError, match="invalid_oracle_request"):
            oracle._connection_arguments(bad)


class _Conn:
    def __init__(
        self,
        value: object = "caller",
        close_error: Exception | None = None,
        close_started: asyncio.Event | None = None,
        close_release: asyncio.Event | None = None,
    ) -> None:
        self.value = value
        self.close_error = close_error
        self.close_started = close_started
        self.close_release = close_release
        self.closed = False

    async def fetchval(self, query: str) -> object:
        assert query == "SELECT current_user"
        return self.value

    async def close(self) -> None:
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _install_connect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connection: _Conn | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def connect(**kwargs: Any) -> _Conn:
        seen.update(kwargs)
        if error is not None:
            raise error
        assert connection is not None
        return connection

    monkeypatch.setattr(oracle.asyncpg, "connect", connect)
    original = ssl.create_default_context
    monkeypatch.setattr(oracle.ssl, "create_default_context", lambda cafile: original())
    return seen


def test_run_connection_reports_connected_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn()
    seen = _install_connect(monkeypatch, connection=connection)
    result = asyncio.run(oracle._run_connection(_REQUEST))
    assert result == oracle.OracleResult("connected", current_user="caller")
    assert connection.closed
    assert seen["host"] == "database.example.test"
    assert seen["ssl"].check_hostname is True


def test_run_connection_reports_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn(close_error=RuntimeError("close"))
    _install_connect(monkeypatch, connection=connection)
    assert asyncio.run(oracle._run_connection(_REQUEST)).kind == "other_failure"
    assert connection.closed


def test_run_connection_retains_close_through_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        close_started = asyncio.Event()
        close_release = asyncio.Event()
        connection = _Conn(close_started=close_started, close_release=close_release)
        _install_connect(monkeypatch, connection=connection)
        entered: asyncio.Queue[None] = asyncio.Queue()
        cancelled: asyncio.Queue[None] = asyncio.Queue()
        original_shield = asyncio.shield

        async def observed_shield(task: asyncio.Future[Any]) -> Any:
            entered.put_nowait(None)
            try:
                return await original_shield(task)
            except asyncio.CancelledError:
                cancelled.put_nowait(None)
                raise

        monkeypatch.setattr(oracle.asyncio, "shield", observed_shield)
        operation = asyncio.create_task(oracle._run_connection(_REQUEST))
        await close_started.wait()
        await entered.get()
        operation.cancel()
        await cancelled.get()
        await entered.get()
        operation.cancel()
        await cancelled.get()
        assert not operation.done()
        assert not connection.closed
        close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert connection.closed

    asyncio.run(run())


def test_run_connection_rejects_non_text_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn(value=7)
    _install_connect(monkeypatch, connection=connection)
    assert asyncio.run(oracle._run_connection(_REQUEST)) == oracle.OracleResult("other_failure")
    assert connection.closed


@pytest.mark.parametrize(
    ("error", "kind", "sqlstate"),
    [
        (asyncpg.InvalidPasswordError("x"), "auth_refused", "28P01"),
        (ConnectionRefusedError("x"), "other_failure", None),
        (ssl.SSLError("x"), "other_failure", None),
    ],
)
def test_run_connection_maps_connect_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str, sqlstate: str | None
) -> None:
    _install_connect(monkeypatch, error=error)
    expected = oracle.OracleResult(cast(Any, kind), sqlstate=sqlstate)
    assert asyncio.run(oracle._run_connection(_REQUEST)) == expected


def test_run_connection_refuses_driver_drift_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_connect(monkeypatch, connection=_Conn())
    monkeypatch.setattr(oracle.asyncpg, "__version__", "0.0.1")
    result = asyncio.run(oracle._run_connection(_REQUEST))
    assert result == oracle.OracleResult("other_failure", driver_version="0.0.1")
    assert seen == {}


def test_encode_result_is_compact_and_bounded() -> None:
    line = oracle._encode_result(oracle.OracleResult("auth_refused", sqlstate="28000"))
    assert line.endswith(b"\n")
    assert json.loads(line) == {
        "kind": "auth_refused",
        "current_user": None,
        "sqlstate": "28000",
        "driver_version": _VERSION,
    }
    with pytest.raises(RuntimeError, match="oracle_output_limit"):
        oracle._encode_result(
            oracle.OracleResult("other_failure", driver_version="v" * oracle.MAX_OUTPUT_BYTES)
        )


def _child_output(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> dict[str, Any]:
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=stdout))
    assert asyncio.run(oracle._child_main(raw)) == 0
    return json.loads(stdout.getvalue())


def test_child_main_runs_request_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_connect(monkeypatch, connection=_Conn())
    assert _child_output(monkeypatch, json.dumps(_REQUEST).encode())["kind"] == "connected"


def test_child_main_reports_observation_expiry_with_private_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=stdout))

    async def expired(_request: object) -> oracle.OracleResult:
        raise oracle._OracleObservationExpired

    monkeypatch.setattr(oracle, "_run_connection", expired)
    result = asyncio.run(oracle._child_main(json.dumps(_REQUEST).encode()))
    assert result == oracle._OBSERVATION_EXPIRED_EXIT_CODE
    assert json.loads(stdout.getvalue()) == {
        "kind": "other_failure",
        "current_user": None,
        "sqlstate": None,
        "driver_version": _VERSION,
    }


def test_decode_accepts_only_exact_private_expiry_report() -> None:
    with pytest.raises(LiveQualificationIncomplete) as info:
        oracle._decode_output(
            _report(kind="other_failure", current_user=None),
            oracle._OBSERVATION_EXPIRED_EXIT_CODE,
        )
    assert info.value.reason == "qualification_database_credential_not_observed"
    for raw in (
        _report(kind="connected", current_user="caller"),
        _report(kind="auth_refused", current_user=None, sqlstate="28P01"),
        _report(kind="other_failure", current_user=None, driver_version="0.31.0"),
    ):
        with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
            oracle._decode_output(raw, oracle._OBSERVATION_EXPIRED_EXIT_CODE)


@pytest.mark.parametrize(
    "raw",
    [b"x" * (oracle.MAX_INPUT_BYTES + 1), b"not-json", json.dumps({"host": "h"}).encode()],
    ids=["oversize", "malformed", "invalid-request"],
)
def test_child_main_collapses_bad_input_to_other_failure(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    report = _child_output(monkeypatch, raw)
    assert (report["kind"], report["sqlstate"], report["current_user"]) == (
        "other_failure",
        None,
        None,
    )


def test_child_environment_is_minimal() -> None:
    environment = oracle._child_environment()
    assert set(environment) == {"PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    assert environment["PYTHONPATH"] == str(oracle.PROJECT_ROOT)
    assert str(oracle.Path(sys.executable).absolute().parent) in environment["PATH"]


def test_read_child_output_enforces_bound() -> None:
    async def read(data: bytes) -> bytes:
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()
        return await oracle._read_child_output(stream)

    assert asyncio.run(read(b"x" * oracle.MAX_OUTPUT_BYTES)) == b"x" * oracle.MAX_OUTPUT_BYTES
    with pytest.raises(RuntimeError, match="database_oracle_output_limit"):
        asyncio.run(read(b"x" * (oracle.MAX_OUTPUT_BYTES + 1)))


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (_report(kind="other_failure", current_user=None), None),
        (_report(kind="auth_refused", current_user=None, sqlstate="28P01"), None),
        (_report(), None),
        (b"", "database_oracle_child_failed"),
        (_report(), "rc"),
        (_report().rstrip(b"\n"), "database_oracle_child_failed"),
        (b"\xff\n", "database_oracle_child_failed"),
        (b"[]\n", "database_oracle_child_failed"),
        (_report(kind="weird"), "database_oracle_child_failed"),
        (_report(current_user=5), "database_oracle_child_failed"),
        (_report(sqlstate=5), "database_oracle_child_failed"),
        (_report(sqlstate="28P01"), "database_oracle_child_failed"),
        (
            _report(kind="auth_refused", current_user=None, sqlstate="42P01"),
            "database_oracle_child_failed",
        ),
        (_report(kind="other_failure", current_user="caller"), "database_oracle_child_failed"),
        (
            _report(kind="other_failure", current_user=None, sqlstate="28P01"),
            "database_oracle_child_failed",
        ),
        (b"x" * (oracle.MAX_OUTPUT_BYTES + 1) + b"\n", "database_oracle_child_failed"),
    ],
)
def test_decode_output_validates_every_field(raw: bytes, code: str | None) -> None:
    if code is None:
        assert oracle._decode_output(raw, 0).driver_version == _VERSION
    elif code == "rc":
        with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
            oracle._decode_output(raw, 1)
    else:
        with pytest.raises(RuntimeError, match=code):
            oracle._decode_output(raw, 0)


def _spawn_blocked_child() -> Any:
    async def spawn() -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stdin.read()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    return spawn()


def test_kill_child_kills_group_and_reaps_real_process() -> None:
    async def run() -> None:
        process = await _spawn_blocked_child()
        assert process.returncode is None
        await oracle._kill_child(process)
        assert process.returncode is not None
        assert process.returncode < 0
        # An already-exited child is only awaited, never signalled again.
        await oracle._kill_child(process)

    asyncio.run(run())


def test_cleanup_child_kills_live_process_and_drains_reader() -> None:
    async def run() -> None:
        process = await _spawn_blocked_child()
        assert process.stdout is not None
        reader = asyncio.create_task(oracle._read_child_output(process.stdout))
        await oracle._cleanup_child(process, reader)
        assert process.returncode is not None
        assert await reader == b""

    asyncio.run(run())


def test_cleanup_child_without_reader_on_exited_process() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await process.wait()
        await oracle._cleanup_child(process, None)

    asyncio.run(run())


def test_await_cleanup_finishes_through_cancellation_and_surfaces_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        release = asyncio.Event()
        finished: list[str] = []
        cancelled: asyncio.Queue[None] = asyncio.Queue()
        entered: asyncio.Queue[None] = asyncio.Queue()
        shield = asyncio.shield

        async def observed_shield(task: asyncio.Future[Any]) -> Any:
            entered.put_nowait(None)
            try:
                return await shield(task)
            except asyncio.CancelledError:
                cancelled.put_nowait(None)
                raise

        monkeypatch.setattr(oracle.asyncio, "shield", observed_shield)

        async def cleanup() -> None:
            await release.wait()
            finished.append("done")

        task = asyncio.create_task(cleanup())
        waiter = asyncio.create_task(oracle._await_cleanup(task))
        await entered.get()
        waiter.cancel()
        await cancelled.get()
        await entered.get()
        waiter.cancel()
        await cancelled.get()
        assert not waiter.done()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert finished == ["done"]

        async def broken() -> None:
            raise RuntimeError("cleanup_failed")

        with pytest.raises(RuntimeError, match="cleanup_failed"):
            await oracle._await_cleanup(asyncio.create_task(broken()))

    asyncio.run(run())
