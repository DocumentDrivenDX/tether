"""Offline command, restart and platform-preflight controls."""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from qualify_live_apps_lifecycle import (
    AppLogMonitor,
    _admin_cli,
    _admin_environment,
    _wait_app_state,
    shutdown_report,
)
from qualify_web_platform import (
    Profile,
    _check_app,
    profile_from_environment,
    qualify,
)
from qualify_web_platform import _wait_app_state as wait_sync


def _profile() -> Profile:
    return Profile(
        app="fabricated-app",
        url="https://apps.example",
        pg_host="db.invalid",
        pg_database="fabricated",
        pg_schema="qualification",
        pg_endpoint="fabricated-endpoint",
        ca_file="fabricated-ca.pem",
        api_prefix="/api/v1",
        hold_seconds=0,
    )


class _FakeProcess:
    def __init__(self, *, stdout: bytes = b"{}", returncode: int | None = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.killed = False
        self.terminated = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, b"private stderr marker"

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def terminate(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        self.waited = True
        return int(self.returncode or 0)


def test_admin_environment_keeps_only_safe_inherited_values() -> None:
    source = {
        "PATH": "/bin",
        "HOME": "/home/user",
        "TMPDIR": "/private/home/user/tmp",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
        "DATABRICKS_CLIENT_SECRET": "fabricated-marker",
        "DATABRICKS_CONFIG_FILE": "/private/caller-config",
    }

    clean = _admin_environment(source, None)
    assert clean == {
        "PATH": "/bin",
        "HOME": "/home/user",
        "TMPDIR": "/private/home/user/tmp",
        "SSL_CERT_FILE": "/etc/ssl/cert.pem",
    }
    assert _admin_environment(source, "/private/admin-config")["DATABRICKS_CONFIG_FILE"] == (
        "/private/admin-config"
    )


def test_async_admin_cli_builds_explicit_profile_command_and_parses_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    process = _FakeProcess(stdout=b'{"result":"ok"}')
    observed: dict[str, Any] = {}

    async def create(*args: object, **kwargs: object) -> _FakeProcess:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return process

    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "/bin/databricks")
    monkeypatch.setattr(lifecycle.asyncio, "create_subprocess_exec", create)

    result = asyncio.run(
        _admin_cli(
            "fabricated-admin",
            "/private/admin-config",
            {"PATH": "/bin", "DATABRICKS_CLIENT_SECRET": "never-inherit"},
            "apps",
            "get",
            "fabricated-app",
        )
    )
    args = observed["args"]
    kwargs = observed["kwargs"]
    assert result == '{"result":"ok"}'
    assert args[0] == "/bin/databricks"
    assert args[-4:] == ("--profile", "fabricated-admin", "-o", "json")
    assert kwargs["env"]["DATABRICKS_CONFIG_FILE"] == "/private/admin-config"
    assert "DATABRICKS_CLIENT_SECRET" not in kwargs["env"]
    assert kwargs["stderr"] is asyncio.subprocess.DEVNULL


@pytest.mark.parametrize(
    ("which", "returncode", "expected"),
    [
        (None, 0, "databricks_cli_unavailable"),
        ("/bin/databricks", 1, "qualification_admin_cli_failed"),
    ],
)
def test_async_admin_cli_fails_closed_without_echoing_process_output(
    monkeypatch: pytest.MonkeyPatch, which: str | None, returncode: int, expected: str
) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    process = _FakeProcess(stdout=b"secret marker", returncode=returncode)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: which)

    async def create(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    monkeypatch.setattr(lifecycle.asyncio, "create_subprocess_exec", create)

    async def run() -> None:
        with pytest.raises(RuntimeError, match=expected):
            await _admin_cli("admin", None, {}, "apps", "get", "app")

    asyncio.run(run())


def test_async_admin_cli_kills_and_joins_timed_out_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    process = _FakeProcess()
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "/bin/databricks")

    async def create(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    async def timeout(_awaitable: object, _seconds: float) -> object:
        if inspect.iscoroutine(_awaitable):
            _awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(lifecycle.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(lifecycle.asyncio, "wait_for", timeout)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_admin_cli_timeout"):
            await _admin_cli("admin", None, {}, "apps", "get", "app")

    asyncio.run(run())
    assert process.killed
    assert process.waited


def test_monitor_timeout_classifies_parse_failure_and_log_eof() -> None:
    async def monitor_with(
        *, parse_failed: bool, reader_done: bool, returncode: int | None
    ) -> tuple[str, _FakeProcess]:
        loop = asyncio.get_running_loop()
        report: asyncio.Future[dict[str, Any]] = loop.create_future()
        process = _FakeProcess(returncode=returncode or 0)
        monitor = AppLogMonitor(
            cast(Any, process),
            "call",
            asyncio.Event(),
            report,
            reader=loop.create_task(asyncio.sleep(0 if reader_done else 60)),
            shutdown_marker_parse_failed=parse_failed,
        )
        if reader_done:
            await asyncio.sleep(0)
        try:
            await monitor.wait_shutdown_report(timeout_seconds=0)
        except RuntimeError as error:
            return str(error), process
        raise AssertionError("monitor timeout unexpectedly succeeded")

    assert asyncio.run(monitor_with(parse_failed=True, reader_done=False, returncode=None))[0] == (
        "live_shutdown_report_malformed"
    )
    assert asyncio.run(monitor_with(parse_failed=False, reader_done=True, returncode=0))[0] == (
        "live_shutdown_log_stream_ended"
    )
    assert asyncio.run(monitor_with(parse_failed=False, reader_done=True, returncode=4))[0] == (
        "live_shutdown_log_follow_failed"
    )
    assert asyncio.run(monitor_with(parse_failed=False, reader_done=False, returncode=None))[0] == (
        "live_shutdown_report_missing"
    )


def test_monitor_close_terminates_process_and_waits_reader() -> None:
    async def run() -> tuple[bool, bool]:
        loop = asyncio.get_running_loop()
        reader: asyncio.Task[None] = loop.create_task(asyncio.sleep(0))
        process = _FakeProcess(returncode=None)
        monitor = AppLogMonitor(
            cast(Any, process), "call", asyncio.Event(), loop.create_future(), reader=reader
        )
        await monitor.aclose()
        return process.terminated, reader.done()

    assert asyncio.run(run()) == (True, True)


def test_monitor_close_kills_process_when_reader_does_not_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    async def run() -> _FakeProcess:
        finished = asyncio.Event()

        async def wait_for_reader() -> None:
            await finished.wait()

        task = asyncio.create_task(wait_for_reader())
        process = _FakeProcess(returncode=None)
        monitor = AppLogMonitor(
            cast(Any, process),
            "call",
            asyncio.Event(),
            asyncio.get_running_loop().create_future(),
            reader=cast(Any, task),
        )

        async def wait_for(awaitable: object, *_args: object, **_kwargs: object) -> None:
            del awaitable
            finished.set()
            await task
            raise TimeoutError

        monkeypatch.setattr(lifecycle.asyncio, "wait_for", wait_for)
        await monitor.aclose()
        return process

    process = asyncio.run(run())
    assert process.terminated
    assert process.killed


def test_async_wait_app_state_returns_for_accepted_pair_and_times_out_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    calls = 0

    async def get_state(*_args: object, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {"app_status": {"state": "STOPPED"}, "compute_status": {"state": "STOPPED"}}
        )

    monkeypatch.setattr(lifecycle, "_admin_cli", get_state)
    asyncio.run(
        _wait_app_state("admin", None, {}, "app", {("STOPPED", "STOPPED")}, timeout_seconds=1)
    )
    assert calls == 1
    with pytest.raises(RuntimeError, match="qualification_app_state_timeout"):
        asyncio.run(
            _wait_app_state("admin", None, {}, "app", {("RUNNING", "ACTIVE")}, timeout_seconds=0)
        )


@pytest.mark.parametrize(
    ("logs", "expected"),
    [
        ("ordinary log", "live_shutdown_report_missing"),
        ("TETHER_SHUTDOWN_REPORT []", "live_shutdown_report_missing"),
        (
            'TETHER_SHUTDOWN_REPORT {"ok":1}\nTETHER_SHUTDOWN_REPORT invalid',
            "live_shutdown_report_missing",
        ),
        ('TETHER_SHUTDOWN_REPORT {"ok":1}', "ok"),
    ],
)
def test_shutdown_report_scans_newest_parseable_object_only(logs: str, expected: str) -> None:
    if expected == "ok":
        assert shutdown_report(logs) == {"ok": 1}
    else:
        with pytest.raises(RuntimeError, match=expected):
            shutdown_report(logs)


def test_platform_profile_inputs_are_validated_before_process_calls() -> None:
    base = {
        "TETHER_QUALIFICATION_APP": "fabricated-app",
        "TETHER_QUALIFICATION_APP_URL": "https://apps.example",
        "TETHER_PG_HOST": "db.invalid",
        "TETHER_PG_DATABASE": "fabricated",
        "TETHER_PG_SCHEMA": "schema_ok",
        "TETHER_PG_ENDPOINT": "fake-endpoint",
        "TETHER_PG_SSLROOTCERT": "fake-ca.pem",
    }
    assert profile_from_environment(base).scenario == "basic"
    for key, value, message in (
        ("TETHER_QUALIFICATION_APP_URL", "http://apps.example", "qualification_app_url_invalid"),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://" + "".join(("us", "er", ":", "se", "cret", "@", "apps.example")),
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example?next=/other",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example?",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example/#section",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example#",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example:invalid",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example:",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://:443",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://[broken",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.example" + "\\" + "@" + "evil.example",
            "qualification_app_url_invalid",
        ),
        (
            "TETHER_QUALIFICATION_APP_URL",
            "https://apps.\nexample",
            "qualification_app_url_invalid",
        ),
        ("TETHER_API_PREFIX", "/bad/", "qualification_api_prefix_invalid"),
        ("TETHER_QUALIFICATION_HOLD_SECONDS", "nan", "qualification_hold_invalid"),
        ("TETHER_QUALIFICATION_SCENARIO", "unknown", "qualification_scenario_invalid"),
    ):
        changed = dict(base, **{key: value})
        with pytest.raises(ValueError, match=message):
            profile_from_environment(changed)


def test_check_app_rejects_each_deployment_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_web_platform as platform

    settings = _profile()
    cli_values = iter(
        [
            json.dumps({"app_status": {"state": "STOPPED"}}),
            json.dumps({"app_status": {"state": "RUNNING"}}),
            json.dumps({"deployments": []}),
            json.dumps({"app_status": {"state": "RUNNING"}}),
            json.dumps({"deployments": [{"status": {"state": "FAILED"}}]}),
        ]
    )
    monkeypatch.setattr(platform, "_cli", lambda *_args, **_kwargs: next(cli_values))
    with pytest.raises(RuntimeError, match="qualification_app_not_running"):
        _check_app("p", settings)
    with pytest.raises(RuntimeError, match="qualification_deployment_missing"):
        _check_app("p", settings)
    with pytest.raises(RuntimeError, match="qualification_deployment_not_succeeded"):
        _check_app("p", settings)


def test_platform_cli_wait_app_state_success_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_web_platform as platform

    monkeypatch.setattr(
        platform,
        "_cli",
        lambda *_args, **_kwargs: json.dumps(
            {"app_status": {"state": "RUNNING"}, "compute_status": {"state": "ACTIVE"}}
        ),
    )
    wait_sync("p", "app", {("RUNNING", "ACTIVE")}, timeout=1)
    with pytest.raises(RuntimeError, match="qualification_app_state_timeout"):
        wait_sync("p", "app", {("STOPPED", "STOPPED")}, timeout=0)


def test_restart_app_stops_log_process_and_requires_healthy_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_web_platform as platform

    settings = _profile()
    process = SimpleNamespace(
        stdout=None,
        stderr=None,
        returncode=0,
        poll=lambda: 0,
        communicate=lambda timeout=None: (
            'TETHER_SHUTDOWN_REPORT {"complete":true,"outstanding":[],"asgi_http_versions":["2"]}',
            "",
        ),
        terminate=lambda: None,
        kill=lambda: None,
    )
    monkeypatch.setattr(platform.shutil, "which", lambda _name: "/bin/databricks")
    monkeypatch.setattr(platform.subprocess, "Popen", lambda *args, **kwargs: process)
    calls: list[tuple[str, ...]] = []

    def cli(profile: str, *args: str, **kwargs: object) -> str:
        del profile, kwargs
        calls.append(args)
        if args[:2] == ("apps", "list-deployments"):
            return "{}"
        return "{}"

    monkeypatch.setattr(platform, "_cli", cli)
    monkeypatch.setattr(platform, "_wait_app_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform.time, "sleep", lambda _seconds: None)
    platform._restart_app("p", settings)
    assert calls[0][:2] == ("apps", "stop")
    assert calls[-1][:2] == ("apps", "start")


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (
            'TETHER_SHUTDOWN_REPORT {"complete":false,"outstanding":["client_host"]}',
            "bounded_shutdown_report_incomplete",
        ),
        (
            'TETHER_SHUTDOWN_REPORT {"complete":true,"outstanding":[],"asgi_http_versions":["1"]}',
            "apps_backend_http2_not_observed",
        ),
        ("TETHER_SHUTDOWN_REPORT {bad json", "bounded_shutdown_report_incomplete"),
    ],
)
def test_restart_app_rejects_incomplete_or_wrong_protocol_report(
    monkeypatch: pytest.MonkeyPatch, log: str, expected: str
) -> None:
    import qualify_web_platform as platform

    process = SimpleNamespace(
        stdout=None,
        stderr=None,
        returncode=0,
        poll=lambda: 0,
        communicate=lambda timeout=None: (log, ""),
        terminate=lambda: None,
        kill=lambda: None,
    )
    monkeypatch.setattr(platform.shutil, "which", lambda _name: "/bin/databricks")
    monkeypatch.setattr(platform.subprocess, "Popen", lambda *_args, **_kwargs: process)
    starts: list[bool] = []
    monkeypatch.setattr(
        platform,
        "_cli",
        lambda _profile, *args, **_kwargs: starts.append(args[1] == "start") or "{}",
    )
    monkeypatch.setattr(platform, "_wait_app_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match=expected):
        platform._restart_app("p", _profile())
    assert any(starts)


@pytest.mark.parametrize(
    ("status", "http_version", "expected"),
    [(401, "HTTP/2", None), (200, "HTTP/1.1", "apps_ingress_http2_auth_gate_failed")],
)
def test_platform_qualify_rejects_ingress_before_database_or_remote(
    monkeypatch: pytest.MonkeyPatch, status: int, http_version: str, expected: str | None
) -> None:
    import qualify_web_platform as platform

    calls: list[str] = []
    monkeypatch.setattr(platform, "_check_app", lambda *_args: calls.append("app"))

    class Response:
        def __init__(self) -> None:
            self.status_code = status
            self.http_version = http_version

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object) -> Response:
            return Response()

    monkeypatch.setattr(platform.httpx, "AsyncClient", Client)

    async def database(*_args: object) -> None:
        calls.append("database")

    async def remote(*_args: object, **_kwargs: object) -> None:
        calls.append("remote")

    monkeypatch.setattr(platform, "_database_probe", database)
    monkeypatch.setattr(platform, "_remote_probe", remote)
    operation = qualify("profile", _profile(), restart_app=False)
    if expected is None:
        asyncio.run(operation)
        assert calls == ["app", "database", "remote"]
    else:
        with pytest.raises(RuntimeError, match=expected):
            asyncio.run(operation)
        assert calls == ["app"]


def test_platform_qualify_runs_restart_profile_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_web_platform as platform

    calls: list[str] = []
    monkeypatch.setattr(platform, "_check_app", lambda *_args: None)

    class Response:
        status_code, http_version = 200, "HTTP/1.1"

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object) -> Response:
            return Response()

    monkeypatch.setattr(platform.httpx, "AsyncClient", Client)
    monkeypatch.setattr(platform, "_database_probe", lambda *_args: _mark(calls, "database"))
    monkeypatch.setattr(platform, "_remote_probe", lambda *_args, **_kwargs: _mark(calls, "remote"))
    with pytest.raises(RuntimeError, match="apps_ingress_http2_auth_gate_failed"):
        asyncio.run(qualify("profile", _profile(), restart_app=True))
    assert calls == []


async def _mark(calls: list[str], name: str) -> None:
    calls.append(name)
