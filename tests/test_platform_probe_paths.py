"""Platform qualifier CLI, database and remote probes over fabricated boundaries."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types
from typing import Any

import pytest
import qualify_web_platform as platform
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_web_platform import Profile, _check_app, _cli, _database_probe, _remote_probe

_OK_VALUE = "-".join(("fabricated", "value"))


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


def test_cli_requires_installed_tool_and_builds_explicit_profile_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="databricks_cli_unavailable"):
        _cli("p", "apps", "get")
    seen: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"ok": true}')

    monkeypatch.setattr(platform.shutil, "which", lambda _name: "/bin/databricks")
    monkeypatch.setattr(platform.subprocess, "run", run)
    assert _cli("p", "apps", "get", "app", timeout_seconds=3) == '{"ok": true}'
    assert seen["command"] == [
        "/bin/databricks",
        "apps",
        "get",
        "app",
        "--profile",
        "p",
        "-o",
        "json",
    ]
    assert seen["kwargs"]["timeout"] == 3
    assert seen["kwargs"]["check"] is True


@pytest.mark.parametrize("wrapped", [True, False])
def test_check_app_accepts_running_succeeded_latest_deployment(
    monkeypatch: pytest.MonkeyPatch, wrapped: bool
) -> None:
    deployments: Any = [{"status": {"state": "SUCCEEDED"}}]
    answers = iter(
        [
            json.dumps({"app_status": {"state": "RUNNING"}}),
            json.dumps({"deployments": deployments} if wrapped else deployments),
        ]
    )
    monkeypatch.setattr(platform, "_cli", lambda *_a, **_k: next(answers))
    _check_app("p", _profile())
    assert next(answers, None) is None


def test_check_app_refuses_non_list_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(
        [json.dumps({"app_status": {"state": "RUNNING"}}), json.dumps({"deployments": "x"})]
    )
    monkeypatch.setattr(platform, "_cli", lambda *_a, **_k: next(answers))
    with pytest.raises(RuntimeError, match="qualification_deployment_missing"):
        _check_app("p", _profile())


class _FakeCursor:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: str, parameters: object = None) -> None:
        self.statements.append(statement)
        self.parameters = parameters

    async def fetchone(self) -> Any:
        return self.rows.pop(0)


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True

    def cursor(self) -> _FakeCursor:
        return self._cursor


def _install_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: list[Any],
    token: object = _OK_VALUE,
    user: object = "person@example.invalid",
) -> dict[str, Any]:
    record: dict[str, Any] = {"cursor": _FakeCursor(rows)}

    class Workspace:
        def __init__(self, *, profile: str) -> None:
            record["profile"] = profile
            self.current_user = types.SimpleNamespace(
                me=lambda: types.SimpleNamespace(user_name=user)
            )
            self.postgres = types.SimpleNamespace(
                generate_database_credential=lambda *, endpoint: types.SimpleNamespace(token=token)
            )

    class Connection:
        @staticmethod
        async def connect(conninfo: str) -> _FakeConnection:
            record["conninfo"] = conninfo
            return _FakeConnection(record["cursor"])

    def make_conninfo(**fields: object) -> str:
        record["fields"] = fields
        return "fabricated-conninfo"

    modules = {
        "databricks": types.ModuleType("databricks"),
        "databricks.sdk": types.ModuleType("databricks.sdk"),
        "psycopg": types.ModuleType("psycopg"),
        "psycopg.conninfo": types.ModuleType("psycopg.conninfo"),
    }
    modules["databricks.sdk"].__dict__["WorkspaceClient"] = Workspace
    modules["psycopg"].__dict__["AsyncConnection"] = Connection
    modules["psycopg.conninfo"].__dict__["make_conninfo"] = make_conninfo
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return record


def test_database_probe_accepts_exact_managed_profile_and_rls_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _install_sdk(monkeypatch, rows=[("180002", "off", "off", "on"), (12,)])
    asyncio.run(_database_probe("profile", _profile(), None))
    assert record["profile"] == "profile"
    assert record["fields"]["sslmode"] == "verify-full"
    assert record["fields"]["host"] == "db.invalid"
    assert record["cursor"].parameters == ("qualification",)
    assert len(record["cursor"].statements) == 2


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([None], "managed_postgres_profile_mismatch"),
        ([("170001", "off", "off", "on")], "managed_postgres_profile_mismatch"),
        ([("180002", "on", "off", "on")], "managed_postgres_profile_mismatch"),
        ([("180002", "off", "off", "on"), None], "owner_rls_catalog_incomplete"),
        ([("180002", "off", "off", "on"), (3,)], "owner_rls_catalog_incomplete"),
    ],
)
def test_database_probe_refuses_unqualified_server_and_catalog(
    monkeypatch: pytest.MonkeyPatch, rows: list[Any], reason: str
) -> None:
    _install_sdk(monkeypatch, rows=rows)
    with pytest.raises(RuntimeError, match=reason):
        asyncio.run(_database_probe("profile", _profile(), None))


@pytest.mark.parametrize(
    ("token", "user", "reason"),
    [
        ("", "person@example.invalid", "database_credential_unavailable"),
        (None, "person@example.invalid", "database_credential_unavailable"),
        (_OK_VALUE, "no-at-sign", "database_profile_user_unavailable"),
        (_OK_VALUE, "has space@example.invalid", "database_profile_user_unavailable"),
        (_OK_VALUE, None, "database_profile_user_unavailable"),
    ],
)
def test_database_probe_refuses_unusable_identity_before_connecting(
    monkeypatch: pytest.MonkeyPatch, token: object, user: object, reason: str
) -> None:
    record = _install_sdk(monkeypatch, rows=[], token=token, user=user)
    with pytest.raises(RuntimeError, match=reason):
        asyncio.run(_database_probe("profile", _profile(), None))
    assert "conninfo" not in record


class _Http:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Session:
    id = "session-1"
    last_seq = 4


class _Client:
    """Fabricated remote client; behavior is chosen per scenario."""

    def __init__(
        self, *, result: Any = None, entries: bool = True, view_id: str = "session-1"
    ) -> None:
        self.closed_sessions: list[str] = []
        self.aclosed = False
        self._result = result
        self._entries = entries
        self._view_id = view_id

    async def create(self, **_kwargs: object) -> _Session:
        return _Session()

    async def send(self, _session: str, _text: str, *, request_id: str) -> Any:
        assert request_id.startswith("web-qualification-")
        result = self._result

        async def finish() -> Any:
            return result

        return types.SimpleNamespace(turn_id="turn-1", result=finish)

    async def get(self, _session: str) -> Any:
        return types.SimpleNamespace(summary=types.SimpleNamespace(id=self._view_id))

    async def entries(self, _session: str, *, limit: int) -> Any:
        return types.SimpleNamespace(items=[1] if self._entries else [])

    async def close(self, session_id: str) -> None:
        self.closed_sessions.append(session_id)

    async def aclose(self) -> None:
        self.aclosed = True


def _final(messages: int = 2, stop: str = "final") -> Any:
    return types.SimpleNamespace(stop_reason=stop, messages=[object()] * messages)


def _install_open(monkeypatch: pytest.MonkeyPatch, client: _Client) -> _Http:
    http = _Http()

    async def open_remote(_settings: object) -> tuple[_Client, _Http]:
        return client, http

    monkeypatch.setattr(platform, "_open_remote", open_remote)
    return http


def test_remote_probe_completes_turn_reads_back_and_closes_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(result=_final())
    http = _install_open(monkeypatch, client)
    asyncio.run(_remote_probe("p", _profile(), restart_app=False))
    assert client.closed_sessions == ["session-1"]
    assert client.aclosed
    assert http.closed


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (_Client(result=_final(messages=1)), "remote_turn_did_not_complete"),
        (_Client(result=_final(stop="cancelled")), "remote_turn_did_not_complete"),
        (_Client(result=_final(), entries=False), "remote_session_readback_failed"),
        (_Client(result=_final(), view_id="other"), "remote_session_readback_failed"),
    ],
)
def test_remote_probe_failures_still_close_session_and_transports(
    monkeypatch: pytest.MonkeyPatch, client: _Client, reason: str
) -> None:
    http = _install_open(monkeypatch, client)
    with pytest.raises(RuntimeError, match=reason):
        asyncio.run(_remote_probe("p", _profile(), restart_app=False))
    assert client.closed_sessions == ["session-1"]
    assert client.aclosed
    assert http.closed


def test_remote_probe_restart_uses_recovered_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _Client()
    recovered = _Client()
    _install_open(monkeypatch, original)
    recovered_http = _Http()
    seen: list[tuple[Any, ...]] = []

    async def restart(*args: Any) -> tuple[_Client, _Http]:
        seen.append(args)
        return recovered, recovered_http

    monkeypatch.setattr(platform, "_restart_with_accepted_turn", restart)
    asyncio.run(_remote_probe("p", _profile(), restart_app=True, watchdog_seconds=9))
    assert seen[0][3:] == (_profile(), "session-1", 4, 9)
    assert recovered.closed_sessions == ["session-1"]
    assert recovered.aclosed
    assert recovered_http.closed
    assert not original.aclosed


class _RestartClient(_Client):
    def __init__(self, outcome: Any) -> None:
        super().__init__()
        self.outcome = outcome

    async def update(self, _session: str, *, title: str) -> Any:
        self.title = title
        return types.SimpleNamespace(last_seq=9)

    async def turn_result(self, _session: str, _turn: str) -> Any:
        return self.outcome


def _install_restart_boundaries(
    monkeypatch: pytest.MonkeyPatch, reopened: _RestartClient
) -> tuple[list[str], _Http]:
    events: list[str] = []
    reopened_http = _Http()

    async def start_feed(_client: object, _session: str, **kwargs: Any) -> tuple[object, Any]:
        events.append(f"feed:{kwargs['since']}:{kwargs['expected_seq']}")
        return object(), asyncio.create_task(asyncio.sleep(0))

    async def stop_feed(task: Any) -> None:
        events.append("stop")
        await task

    async def active(*_args: object) -> None:
        events.append("active")

    def restart_app(*_args: object) -> None:
        events.append("restart")

    async def open_remote(_settings: object) -> tuple[_RestartClient, _Http]:
        events.append("reopen")
        return reopened, reopened_http

    monkeypatch.setattr(platform, "_start_event_feed", start_feed)
    monkeypatch.setattr(platform, "_stop_event_feed", stop_feed)
    monkeypatch.setattr(platform, "_assert_active_turn", active)
    monkeypatch.setattr(platform, "_restart_app", restart_app)
    monkeypatch.setattr(platform, "_open_remote", open_remote)
    return events, reopened_http


@pytest.mark.parametrize("stop_reason", ["cancelled", "interrupted"])
def test_restart_recovers_terminal_outcome_after_closing_old_observers(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str
) -> None:
    reopened = _RestartClient(types.SimpleNamespace(stop_reason=stop_reason))
    events, _ = _install_restart_boundaries(monkeypatch, reopened)
    old: Any = _RestartClient(None)
    old_http: Any = _Http()
    client, http = asyncio.run(
        platform._restart_with_accepted_turn(old, old_http, "p", _profile(), "session-1", 3, None)
    )
    assert events == ["feed:3:9", "active", "stop", "restart", "reopen"]
    assert old.aclosed
    assert old_http.closed
    assert client is reopened
    assert http is not old_http
    assert old.title == "Temporary web qualification feed ready"


@pytest.mark.parametrize("outcome", [None, types.SimpleNamespace(stop_reason="final")])
def test_restart_refuses_nonterminal_outcome_and_closes_reopened_client(
    monkeypatch: pytest.MonkeyPatch, outcome: Any
) -> None:
    reopened = _RestartClient(outcome)
    old: Any = _RestartClient(None)
    old_http: Any = _Http()
    _, reopened_http = _install_restart_boundaries(monkeypatch, reopened)
    with pytest.raises(RuntimeError, match="accepted_turn_restart_outcome_not_terminal"):
        asyncio.run(
            platform._restart_with_accepted_turn(
                old, old_http, "p", _profile(), "session-1", 3, None
            )
        )
    assert reopened.aclosed
    assert reopened_http.closed


def _run_main(
    monkeypatch: pytest.MonkeyPatch, *argv: str, outcome: BaseException | None = None
) -> tuple[int, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def fake_qualify(profile: str, settings: object, **kwargs: Any) -> None:
        calls.append({"profile": profile, "settings": settings, **kwargs})
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(platform, "profile_from_environment", lambda _values: "settings")
    monkeypatch.setattr(platform, "qualify", fake_qualify)
    monkeypatch.setattr(sys, "argv", ["qualify_web_platform", *argv])
    return platform.main(), calls


def test_main_passes_flags_and_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    code, calls = _run_main(
        monkeypatch, "--profile", "p", "--restart", "--observation-watchdog-seconds", "2.5"
    )
    assert code == 0
    assert calls == [
        {"profile": "p", "settings": "settings", "restart_app": True, "watchdog_seconds": 2.5}
    ]


def test_main_prints_only_registered_codes_and_hides_other_error_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run_main(
        monkeypatch, "--profile", "p", outcome=RuntimeError("qualification_app_not_running")
    )
    assert code == 1
    assert capsys.readouterr().err == "Live profile check failed: qualification_app_not_running\n"
    code, _ = _run_main(
        monkeypatch, "--profile", "p", outcome=RuntimeError("https://host.invalid token")
    )
    assert code == 1
    assert capsys.readouterr().err == "Live profile check failed: RuntimeError\n"
    code, _ = _run_main(monkeypatch, "--profile", "p", outcome=KeyError("secret-marker"))
    assert code == 1
    assert capsys.readouterr().err == "Live profile check failed: KeyError\n"


def test_main_reports_only_exception_type_for_missing_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(platform.os, "environ", {})
    monkeypatch.setattr(sys, "argv", ["qualify_web_platform", "--profile", "p"])
    assert platform.main() == 1
    assert capsys.readouterr().err == "Live profile check failed: ValueError\n"


def test_incomplete_observation_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run_main(
        monkeypatch,
        "--profile",
        "p",
        outcome=LiveQualificationIncomplete("qualification_app_state_not_observed"),
    )
    assert code == 2
    assert "incomplete: qualification_app_state_not_observed" in capsys.readouterr().err


@pytest.mark.parametrize("restart", [False, True])
def test_qualify_reports_each_passed_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], restart: bool
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(platform, "_check_app", lambda *_a: seen.append("app"))

    class Response:
        status_code, http_version = 401, "HTTP/2"

    class Client:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> Response:
            seen.append(url)
            return Response()

    async def database(*_a: object) -> None:
        seen.append("database")

    async def remote(*_a: object, restart_app: bool, watchdog_seconds: float | None) -> None:
        seen.append(f"remote:{restart_app}:{watchdog_seconds}")

    monkeypatch.setattr(platform.httpx, "AsyncClient", Client)
    monkeypatch.setattr(platform, "_database_probe", database)
    monkeypatch.setattr(platform, "_remote_probe", remote)
    asyncio.run(platform.qualify("p", _profile(), restart_app=restart, watchdog_seconds=None))
    assert seen == [
        "app",
        "https://apps.example/api/v1/openapi.json",
        "database",
        f"remote:{restart}:None",
    ]
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == (5 if restart else 4)
    assert (lines[-1] == "app restart and durable turn-result recovery: passed") is restart
