"""Hermetic unit controls for the isolated asyncpg authentication oracle (fabricated, no server)."""

from __future__ import annotations

import asyncio
import io
import json
import ssl
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import asyncpg
import pytest
import selected_profiles_pg_oracle as oracle

_VERSION = oracle.EXPECTED_ASYNCPG_VERSION
_REQUEST: dict[str, Any] = {
    "host": "database.example.test",
    "port": 5432,
    "user": "caller",
    "password": "-".join(("fabricated", "value")),
    "database": "db",
    "ca_file": "/fabricated/ca.pem",
}


def _report(**override: object) -> bytes:
    value = {
        "kind": "connected",
        "current_user": "caller",
        "sqlstate": None,
        "driver_version": _VERSION,
        **override,
    }
    return json.dumps(value).encode() + b"\n"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (asyncpg.InvalidPasswordError("x"), "28P01"),
        (asyncpg.InvalidAuthorizationSpecificationError("x"), "28000"),
        (asyncpg.UndefinedTableError("x"), None),
        (OSError("x"), None),
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
    def __init__(self, value: object = "caller", close_error: Exception | None = None) -> None:
        self.value = value
        self.close_error = close_error
        self.closed = False

    async def fetchval(self, query: str) -> object:
        assert query == "SELECT current_user"
        return self.value

    async def close(self) -> None:
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


def test_run_connection_suppresses_close_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn(close_error=RuntimeError("close"))
    _install_connect(monkeypatch, connection=connection)
    assert asyncio.run(oracle._run_connection(_REQUEST)).kind == "connected"
    assert connection.closed


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


def test_cleanup_child_kills_live_process_and_cancels_reader() -> None:
    async def run() -> None:
        process = await _spawn_blocked_child()
        assert process.stdout is not None
        reader = asyncio.create_task(oracle._read_child_output(process.stdout))
        await asyncio.sleep(0)
        await oracle._cleanup_child(process, reader)
        assert process.returncode is not None
        assert reader.done()

    asyncio.run(run())


def test_cleanup_child_without_reader_on_exited_process() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await process.wait()
        await oracle._cleanup_child(process, None)

    asyncio.run(run())


def test_await_cleanup_finishes_through_cancellation_and_surfaces_failure() -> None:
    async def run() -> None:
        release = asyncio.Event()
        finished: list[str] = []

        async def cleanup() -> None:
            await release.wait()
            finished.append("done")

        task = asyncio.create_task(cleanup())
        waiter = asyncio.create_task(oracle._await_cleanup(task))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert finished == ["done"]

        async def broken() -> None:
            raise RuntimeError("cleanup_failed")

        with pytest.raises(RuntimeError, match="cleanup_failed"):
            await oracle._await_cleanup(asyncio.create_task(broken()))

    asyncio.run(run())


class _Stdin:
    def __init__(self) -> None:
        self.payload = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.payload += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Child:
    pid = 4242

    def __init__(self, output: bytes, returncode: int | None, *, pipes: bool = True) -> None:
        self.stdin = _Stdin() if pipes else None
        self.stdout = asyncio.StreamReader() if pipes else None
        if self.stdout is not None:
            self.stdout.feed_data(output)
            self.stdout.feed_eof()
        self._final = returncode
        self.returncode: int | None = None if returncode is None else returncode

    async def wait(self) -> int:
        return self._final if self._final is not None else 0


def _probe(**override: Any) -> Any:
    arguments = {k: v for k, v in _REQUEST.items()} | override
    return oracle.probe_database(**arguments)


def _install_spawn(
    monkeypatch: pytest.MonkeyPatch, make: Callable[[], _Child]
) -> tuple[list[Any], list[_Child]]:
    spawned: list[Any] = []
    children: list[_Child] = []

    async def spawn(*args: Any, **kwargs: Any) -> _Child:
        spawned.append((args, kwargs))
        children.append(make())
        return children[-1]

    monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
    return spawned, children


def test_probe_database_sends_request_and_decodes_child_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned, children = _install_spawn(
        monkeypatch,
        lambda: _Child(_report(kind="auth_refused", current_user=None, sqlstate="28P01"), 0),
    )
    result = asyncio.run(_probe())
    assert result == oracle.OracleResult("auth_refused", sqlstate="28P01")
    stdin = children[0].stdin
    assert stdin is not None
    assert json.loads(stdin.payload) == _REQUEST
    assert stdin.closed
    assert spawned[0][0][-1] == "scripts.selected_profiles_pg_oracle"
    assert spawned[0][1]["env"] == oracle._child_environment()


def test_probe_database_refuses_oversize_input(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned, _children = _install_spawn(monkeypatch, lambda: _Child(b"", 0))
    monkeypatch.setattr(oracle, "MAX_INPUT_BYTES", 10)
    with pytest.raises(RuntimeError, match="database_oracle_input_limit"):
        asyncio.run(_probe())
    assert spawned == []


def test_probe_database_refuses_missing_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_spawn(monkeypatch, lambda: _Child(b"", 0, pipes=False))
    with pytest.raises(RuntimeError, match="database_oracle_pipe_unavailable"):
        asyncio.run(_probe())


def test_probe_database_refuses_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_spawn(monkeypatch, lambda: _Child(_report(), 3))
    with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
        asyncio.run(_probe())


def test_probe_database_refuses_child_without_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Unfinished(_Child):
        async def wait(self) -> int:
            # Report completion but leave returncode unset; mark exited at cleanup time.
            self.returncode = None
            return 0

    children: list[_Child] = []

    def make() -> _Child:
        children.append(Unfinished(_report(), 0))
        children[0].returncode = None
        return children[0]

    _install_spawn(monkeypatch, make)

    async def run() -> None:
        try:
            await _probe()
        finally:
            children[0].returncode = 0

    with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
        asyncio.run(run())


def test_probe_database_maps_deadline_to_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(oracle, "CHILD_TIMEOUT_SECONDS", 0.01)

        async def spawn(*_args: Any, **_kwargs: Any) -> _Child:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
        await _probe()

    with pytest.raises(RuntimeError, match="database_oracle_timeout"):
        asyncio.run(run())


@pytest.mark.parametrize(
    ("result", "ok"),
    [
        (oracle.OracleResult("auth_refused", sqlstate="28P01"), True),
        (oracle.OracleResult("connected", current_user="caller"), False),
        (oracle.OracleResult("other_failure"), False),
    ],
)
def test_require_authentication_refusal_returns_only_classified_refusals(
    monkeypatch: pytest.MonkeyPatch, result: oracle.OracleResult, ok: bool
) -> None:
    async def probe(**_kwargs: object) -> oracle.OracleResult:
        return result

    monkeypatch.setattr(oracle, "probe_database", probe)
    if ok:
        assert asyncio.run(oracle.require_authentication_refusal(**_REQUEST)) == (
            "28P01",
            _VERSION,
        )
    else:
        with pytest.raises(RuntimeError, match="database_auth_refusal_unclassified"):
            asyncio.run(oracle.require_authentication_refusal(**_REQUEST))
