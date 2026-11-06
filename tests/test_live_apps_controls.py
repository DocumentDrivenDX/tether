"""Offline control-flow tests for the opt-in live Apps qualifier."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from qualify_live_apps import (
    ROOT,
    _qualify_shared_session,
    _run,
    _run_case,
    main,
)
from qualify_live_apps_config import LiveSettings
from qualify_live_apps_diagnostics import (
    _browser_stage,
    _safe_http_event,
    _safe_network_summary,
    _safe_test_outcome,
    read_rendezvous,
)
from qualify_live_apps_evidence import (
    LIVE_CASE_IDS,
    _browser_cases,
    _runtime_version,
    _versions,
    profile_binding_sha256,
    validate_receipt_output,
    write_live_receipt,
)
from qualify_live_apps_observation import LiveObservationPolicy, use_live_observation_policy
from qualify_live_apps_transport import (
    _browser_environment,
    _client_for,
    _profile_credential,
)
from transport_fixture import shutdown_observation


def _fabricated_authorization() -> str:
    return "".join(("Be", "arer ", "fabricated", "-marker"))


def _settings() -> LiveSettings:
    return LiveSettings("fabricated-app", "https://apps.example", "/prefix", "/api/v1")


def _playwright_report(path: Path, *, outcome: str = "passed") -> None:
    path.write_text(
        json.dumps(
            {
                "config": {"rootDir": str(ROOT)},
                "suites": [
                    {
                        "file": "tests/ui/databricks-live.spec.ts",
                        "specs": [
                            {
                                "title": node_id.split("::", 1)[1],
                                "tests": [
                                    {
                                        "expectedStatus": "passed",
                                        "results": [{"status": outcome, "duration": 1}],
                                    }
                                ],
                            }
                            for node_id in LIVE_CASE_IDS
                        ],
                    }
                ],
            }
        )
    )


class _Process:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_browser_environment_is_allowlisted_and_marks_optional_projection(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    authorization = _fabricated_authorization()
    env = _browser_environment(
        {
            "PATH": "/bin",
            "HOME": "/home/test",
            "DATABRICKS_CLIENT_SECRET": "do-not-copy",
            "UNRELATED": "do-not-copy-either",
        },
        _settings(),
        authorization,
        tmp_path,
        output,
        True,
    )

    assert env["TETHER_LIVE_API_PREFIX"] == "/prefix/api/v1"
    assert env["TETHER_LIVE_SERVICE_CALLER"] == "1"
    assert env["TETHER_LIVE_AUTHORIZATION"] == authorization
    assert env["PATH"] == "/bin"
    assert "DATABRICKS_CLIENT_SECRET" not in env
    assert "UNRELATED" not in env
    assert (tmp_path / "playwright-output").is_dir()


def test_browser_environment_omits_service_projection_when_disabled(tmp_path: Path) -> None:
    authorization = "".join(("Be", "arer ", "test", "-marker"))
    env = _browser_environment({}, _settings(), authorization, tmp_path, tmp_path / "r", False)

    assert "TETHER_LIVE_SERVICE_CALLER" not in env
    assert env["TETHER_LIVE_MOUNT_PATH"] == "/prefix"


def test_client_for_closes_http_transport_when_remote_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_transport as transport

    class Http:
        def __init__(self, **kwargs: object) -> None:
            self.options = kwargs
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    http = Http()

    def http_client(**kwargs: object) -> Http:
        del kwargs
        return http

    class Remote:
        @staticmethod
        async def open(*_args: object, **_kwargs: object) -> object:
            raise ValueError("must not leak")

    monkeypatch.setattr(transport.httpx, "AsyncClient", http_client)
    monkeypatch.setattr(transport, "RemoteClient", Remote)

    async def run() -> None:
        with pytest.raises(ValueError, match="must not leak"):
            await _client_for(_settings(), cast(Any, object()), service_caller=False)

    asyncio.run(run())
    assert http.closed


def test_profile_credential_rejects_non_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_live_apps_transport as transport

    class Token:
        def __init__(self, _settings: object) -> None:
            pass

        async def authorization(self) -> str:
            return "Basic fabricated"

    monkeypatch.setattr(transport, "DatabricksCliProfile", Token)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_profile_credential_unavailable"):
            await _profile_credential("fabricated-profile")

    asyncio.run(run())


def test_rendezvous_reader_returns_only_json_objects(tmp_path: Path) -> None:
    (tmp_path / "pending.json").write_text('{"session_id":"fabricated"}')
    process = cast(Any, _Process())

    assert asyncio.run(read_rendezvous(tmp_path, "pending.json", process=process)) == {
        "session_id": "fabricated"
    }


@pytest.mark.parametrize("contents", ["[]", "null", "7"])
def test_rendezvous_reader_rejects_non_object_payloads(tmp_path: Path, contents: str) -> None:
    (tmp_path / "pending.json").write_text(contents)
    with pytest.raises(RuntimeError, match="live_browser_rendezvous_invalid"):
        asyncio.run(read_rendezvous(tmp_path, "pending.json", process=cast(Any, _Process())))


def test_rendezvous_reader_reports_early_child_exit(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="live_browser_exited_before_rendezvous"):
        asyncio.run(
            read_rendezvous(tmp_path, "pending.json", process=cast(Any, _Process(returncode=2)))
        )


def test_rendezvous_timeout_summarizes_bounded_stage_routes_and_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualify_live_apps_diagnostics as diagnostics

    (tmp_path / "browser-stage-unknown.json").write_text('{"value":"pending_loaded"}')
    (tmp_path / "browser-network-unknown.json").write_text(
        json.dumps(
            {
                "requests": [
                    {"method": "GET", "route": "capabilities", "status": 200},
                    {"method": "POST", "route": "turns", "status": 503, "path": "/secret"},
                ]
            }
        )
    )
    (tmp_path / "browser-outcome-unknown.json").write_text(
        json.dumps(
            {
                "title": LIVE_CASE_IDS[1].split("::", 1)[1],
                "status": "failed",
                "expected": "passed",
                "error_count": 1,
                "failure_category": "timeout",
                "failure_line": 44,
                "message": "secret marker",
            }
        )
    )

    async def run() -> str:
        monkeypatch.setattr(diagnostics.asyncio, "to_thread", _direct_to_thread)
        try:
            with use_live_observation_policy(LiveObservationPolicy(0)):
                await diagnostics.read_rendezvous(
                    tmp_path, "unknown-pending.json", process=cast(Any, _Process())
                )
        except RuntimeError as error:
            return str(error)
        raise AssertionError("missing rendezvous did not time out")

    message = asyncio.run(run())
    assert "unknown-pending.json_after_pending_loaded" in message
    assert "capabilities-GET-200-count-1" in message
    assert "turns-POST-503-count-1" in message
    assert "AC8-failed-passed-errors-1-timeout-line-44" in message
    assert "secret" not in message


async def _direct_to_thread(function: Any, /, *args: object, **kwargs: object) -> object:
    return function(*args, **kwargs)


def test_diagnostic_parsers_ignore_private_and_malformed_data(tmp_path: Path) -> None:
    assert _safe_http_event("not a request") is None
    assert _safe_http_event({"method": "PUT", "route": "turns", "status": 200}) is None
    assert _safe_http_event({"method": "GET", "route": "turns", "status": 700}) is None
    assert _safe_http_event({"method": "GET", "route": "turns", "status": True}) is None
    assert asyncio.run(_browser_stage(tmp_path, "shared")) == "unknown"
    (tmp_path / "browser-network-shared.json").write_text("not json")
    assert asyncio.run(_safe_network_summary(tmp_path, "shared")) == "none"
    (tmp_path / "browser-outcome-shared.json").write_text("[]")
    assert asyncio.run(_safe_test_outcome(tmp_path, "shared")) == "invalid"


def test_browser_cases_refuse_missing_duplicate_or_failed_results(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    _playwright_report(report)
    assert len(_browser_cases(report)) == 2
    _playwright_report(report, outcome="skipped")
    with pytest.raises(RuntimeError, match="live_browser_required_cases_not_all_passed"):
        _browser_cases(report)
    report.write_text(json.dumps({"config": {}, "suites": []}))
    with pytest.raises(ValueError, match="empty_or_duplicate_observed_browser_tests"):
        _browser_cases(report)


@pytest.mark.parametrize("value", [None, 5, "a/path", "x" * 81, "secret?token"])
def test_runtime_version_parser_rejects_non_metadata(value: object) -> None:
    with pytest.raises(RuntimeError, match="qualification_runtime_version_invalid"):
        _runtime_version(value)


def test_versions_require_browser_metadata_and_valid_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualify_live_apps_evidence as evidence

    browser = tmp_path / "browser.json"
    browser.write_text("[]")
    with pytest.raises(RuntimeError, match="qualification_browser_runtime_missing"):
        asyncio.run(_versions({}, browser))
    browser.write_text(json.dumps({"playwright": "Playwright 1.0", "chromium": "Chromium 2"}))
    monkeypatch.setattr(evidence, "_tool_version", _tool_version)
    versions = asyncio.run(_versions({}, browser))
    assert versions["playwright"] == "Playwright 1.0"
    assert versions["chromium"] == "Chromium 2"


async def _tool_version(name: str, _source: dict[str, str]) -> str:
    return f"{name} 1.0"


def test_live_receipt_output_refuses_existing_destination_and_allows_ignored_artifact(
    tmp_path: Path,
) -> None:
    ignored = ROOT / "artifacts" / "test-receipt.json"
    validate_receipt_output(ignored)
    with pytest.raises(RuntimeError, match="qualification_receipt_already_exists"):
        validate_receipt_output(tmp_path)


def test_write_receipt_rejects_incomplete_shutdown_before_io(tmp_path: Path) -> None:
    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_shutdown_evidence_incomplete"):
            await write_live_receipt(
                tmp_path / "receipt.json",
                {},
                {},
                tmp_path / "report.json",
                tmp_path / "runtime.json",
                {},
                profile_name="fabricated-profile",
                app_origin="https://apps.example",
                service_caller=False,
            )

    asyncio.run(run())
    assert not (tmp_path / "receipt.json").exists()


def test_write_receipt_detects_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualify_live_apps_evidence as evidence

    report, runtime = tmp_path / "report.json", tmp_path / "runtime.json"
    _playwright_report(report)
    runtime.write_text(json.dumps({"playwright": "Playwright 1", "chromium": "Chromium 1"}))
    monkeypatch.setattr(evidence, "_versions", _versions_fixed)
    snapshots = iter(({"source": "after"},))
    monkeypatch.setattr(evidence, "inventory", lambda _root: next(snapshots))
    shutdown = {
        **shutdown_observation(ROOT),
        "signal_name": "SIGTERM",
        "signal_to_report_seconds": 1.0,
        "configured_shutdown_seconds": 14.0,
        "complete": False,
        "outstanding": ["client_host"],
        "asgi_http_versions": ["1.1", "2"],
        "runtime": {"python": "3.11"},
    }

    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_source_changed_during_live_run"):
            await write_live_receipt(
                tmp_path / "receipt.json",
                {"source": "before"},
                {},
                report,
                runtime,
                shutdown,
                profile_name="fabricated-profile",
                app_origin="https://apps.example",
                service_caller=False,
            )

    asyncio.run(run())


async def _versions_fixed(_source: dict[str, str], _path: Path) -> dict[str, object]:
    return {"python": "3.11", "node": "22"}


def test_live_receipt_accepts_http_version_and_shutdown_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qualify_live_apps_evidence as evidence

    report, runtime, output = (
        tmp_path / "report.json",
        tmp_path / "runtime.json",
        tmp_path / "out.json",
    )
    _playwright_report(report)
    runtime.write_text("{}")
    monkeypatch.setattr(evidence, "_versions", _versions_fixed)
    monkeypatch.setattr(evidence, "inventory", lambda _root: {"same": "digest"})
    monkeypatch.setattr(
        evidence, "write_receipt", lambda path, value: path.write_text(json.dumps(value))
    )
    shutdown = {
        **shutdown_observation(ROOT),
        "signal_name": "SIGTERM",
        "signal_to_report_seconds": 3.0,
        "signal_to_main_return_seconds": 3.1,
        "configured_shutdown_seconds": 14.0,
        "complete": False,
        "outstanding": ["client_host", "postgres_root"],
        "asgi_http_versions": ["1.1", "2"],
        "runtime": {
            "python": "3.11",
            "untrusted_extra": "fabricated-private-marker",
            "distributions": {
                "hypercorn": "0.18.0",
                "unknown-package": "fabricated-private-marker",
            },
        },
        "untrusted_extra": "fabricated-private-marker",
    }

    asyncio.run(
        write_live_receipt(
            output,
            {"same": "digest"},
            {},
            report,
            runtime,
            shutdown,
            profile_name="fabricated-profile",
            app_origin="https://apps.example",
            service_caller=True,
        )
    )
    saved = json.loads(output.read_text())
    assert "fabricated-private-marker" not in output.read_text()
    assert saved["shutdown"]["observer"]["mode"] == "instrumented-qualification-only"
    assert saved["auth_mode"] == "service-caller"
    assert saved["shutdown"]["outstanding"] == ["client_host", "postgres_root"]
    assert saved["profile_binding_sha256"] == profile_binding_sha256(
        "fabricated-profile", "https://apps.example"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"signal_name": "SIGINT"},
        {"signal_to_report_seconds": 16},
        {"configured_shutdown_seconds": 0},
        {"complete": True},
        {"outstanding": []},
        {"outstanding": ["secret-resource-name"]},
        {"asgi_http_versions": ["9"]},
    ],
)
def test_write_receipt_rejects_each_unqualified_shutdown_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    import qualify_live_apps_evidence as evidence

    monkeypatch.setattr(evidence, "_versions", _versions_fixed)
    shutdown: dict[str, object] = {
        **shutdown_observation(ROOT),
        "signal_name": "SIGTERM",
        "signal_to_report_seconds": 1,
        "configured_shutdown_seconds": 14,
        "complete": False,
        "outstanding": ["client_host"],
        "asgi_http_versions": ["1.1", "2"],
        "runtime": {},
    }
    shutdown.update(changes)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_shutdown_evidence_incomplete"):
            await write_live_receipt(
                tmp_path / "receipt.json",
                {},
                {},
                tmp_path / "report",
                tmp_path / "versions",
                shutdown,
                profile_name="p",
                app_origin="https://origin",
                service_caller=False,
            )

    asyncio.run(run())


def test_live_entrypoint_refuses_output_and_redacts_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import qualify_live_apps as live

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_live_apps",
            "--profile",
            "p",
            "--admin-profile",
            "a",
            "--output",
            str(tmp_path / "receipt"),
        ],
    )
    monkeypatch.setattr(live, "_run", _unexpected_error)
    assert main() == 1
    error = capsys.readouterr().err
    assert "ValueError" in error
    assert "sensitive-value" not in error


async def _unexpected_error(*_args: object, **_kwargs: object) -> None:
    raise ValueError("sensitive-value")


def test_live_run_validates_before_environment_or_cloud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    called = False

    async def should_not_run(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(live, "_run_case", should_not_run)
    output = tmp_path / "exists.json"
    output.write_text("stale")

    async def run() -> None:
        with pytest.raises(RuntimeError, match="qualification_receipt_already_exists"):
            await _run("profile", "admin", None, {}, output, False)

    asyncio.run(run())
    assert not called


def test_qualify_shared_session_checks_winner_and_reported_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def read(_path: Path, name: str, *, process: object) -> dict[str, object]:
        del process
        return (
            {"session_id": "session", "invocation_id": "invocation"}
            if name == "pending.json"
            else {"approval_status": 202}
        )

    async def validate(*_args: object) -> None:
        return None

    async def race(*_args: object) -> bool:
        return False

    monkeypatch.setattr(live, "_read_rendezvous", read)
    monkeypatch.setattr(live, "_validate_shared_session", validate)
    monkeypatch.setattr(live, "_race_approval", race)
    monkeypatch.setattr(live, "_validate_browser_approval", read_approval)
    client = cast(Any, object())
    process = cast(Any, object())
    asyncio.run(_qualify_shared_session(client, process, tmp_path))


async def read_approval(_path: Path, _process: object) -> int:
    return 202


@pytest.mark.parametrize(
    ("remote_won", "browser_status"), [(True, 202), (False, 200), (False, 202)]
)
def test_shared_approval_requires_exactly_one_accepted_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_won: bool, browser_status: int
) -> None:
    import qualify_live_apps as live

    async def read(_path: Path, name: str, *, process: object) -> dict[str, object]:
        del process
        if name == "pending.json":
            return {"session_id": "s", "invocation_id": "i"}
        return {"approval_status": browser_status}

    async def no_error(*_args: object) -> None:
        return None

    async def race(*_args: object) -> bool:
        return remote_won

    async def browser_approval(*_args: object) -> int:
        return browser_status

    monkeypatch.setattr(live, "_read_rendezvous", read)
    monkeypatch.setattr(live, "_validate_shared_session", no_error)
    monkeypatch.setattr(live, "_race_approval", race)
    monkeypatch.setattr(live, "_validate_browser_approval", browser_approval)
    operation = _qualify_shared_session(cast(Any, object()), cast(Any, object()), tmp_path)
    if int(remote_won) + int(browser_status == 202) == 1:
        asyncio.run(operation)
    else:
        with pytest.raises(RuntimeError, match="live_competing_approval_winner_count_invalid"):
            asyncio.run(operation)


def test_run_case_releases_every_owned_client_when_browser_launch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def inventory_async(*_args: object) -> dict[str, str]:
        return {"source": "digest"}

    class Client:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    class Http:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    client, http = Client(), Http()

    async def profile(_profile: str) -> tuple[object, str]:
        return object(), _fabricated_authorization()

    async def open_client(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return client, http

    async def browser(*_args: object) -> object:
        raise RuntimeError("browser launch failed")

    monkeypatch.setattr(live, "inventory", lambda _root: {"source": "digest"})
    monkeypatch.setattr(live, "_profile_credential", profile)
    monkeypatch.setattr(live, "_client_for", open_client)
    monkeypatch.setattr(live, "_launch_browser", browser)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="browser launch failed"):
            await _run_case(
                "caller", "admin", None, {}, _settings(), tmp_path / "receipt.json", False
            )

    asyncio.run(run())
