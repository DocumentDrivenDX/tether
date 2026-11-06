"""Credential separation and parsing checks for the opt-in Apps driver."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from typing import cast

import pytest
from qualify_live_apps import (
    ROOT,
    _stop_browser_group,
)
from qualify_live_apps_config import safe_origin, settings_from_environment
from qualify_live_apps_diagnostics import (
    _safe_http_event,
    _safe_network_summary,
    _safe_test_outcome,
)
from qualify_live_apps_evidence import (
    _browser_cases,
    _runtime_version,
    profile_binding_sha256,
    validate_receipt_output,
)
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_lifecycle import (
    AppLogMonitor,
    _admin_environment,
    _read_app_logs,
    restart_for_unknown_effect,
    shutdown_report,
    start_log_monitor,
)
from qualify_live_apps_observation import LiveObservationPolicy, use_live_observation_policy


@pytest.mark.parametrize(
    "value",
    [
        "".join(("https://", "user", ":", "secret", "@example.test")),
        "https://example.test?debug=1",
        "https://example.test#fragment",
        "https://example.test/path",
        "https://example.test:invalid",
    ],
    ids=["userinfo", "query", "fragment", "path", "invalid-port"],
)
def test_live_origin_rejects_non_origins(value: str) -> None:
    with pytest.raises(RuntimeError, match="qualification_app_url_invalid"):
        safe_origin(value)


def test_live_origin_accepts_only_https_origin() -> None:
    assert safe_origin("https://example.test/") == "https://example.test"


def test_live_restart_settings_need_app_and_endpoint_not_database_bindings() -> None:
    settings = settings_from_environment(
        {
            "TETHER_QUALIFICATION_APP": "fabricated-app-name",
            "TETHER_QUALIFICATION_APP_URL": "https://example.test",
        }
    )

    assert settings.app_name == "fabricated-app-name"
    assert settings.url == "https://example.test"


def test_admin_profile_environment_drops_caller_cli_overrides() -> None:
    source = {
        "PATH": "/usr/bin",
        "DATABRICKS_CONFIG_FILE": "/private/caller-config",
        "DATABRICKS_CLIENT_SECRET": "fabricated-sensitive-value",
        "TETHER_QUALIFICATION_APP": "private-app-reference",
    }

    environment = _admin_environment(source, None)

    assert environment == {
        "PATH": "/usr/bin",
    }
    configured = _admin_environment(source, "/private/admin-config")
    assert configured["DATABRICKS_CONFIG_FILE"] == "/private/admin-config"


def test_shutdown_report_reads_only_json_report_line() -> None:
    report = shutdown_report(
        "ordinary log line\n"
        'TETHER_SHUTDOWN_REPORT {"complete":false,"outstanding":["client_host"]}\n'
    )
    assert report == {"complete": False, "outstanding": ["client_host"]}


def test_shutdown_report_must_follow_this_run_worker_marker() -> None:
    async def run() -> dict[str, object]:
        logs = asyncio.StreamReader()
        logs.feed_data(b'TETHER_SHUTDOWN_REPORT {"complete":true}\n')
        logs.feed_data(b"TETHER_QUALIFICATION_WORKER_STARTED current-call\n")
        logs.feed_data(b'TETHER_SHUTDOWN_REPORT {"complete":false,"signal_name":"SIGTERM"}\n')
        logs.feed_eof()
        monitor = AppLogMonitor(
            process=cast(asyncio.subprocess.Process, SimpleNamespace(stdout=logs)),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        await _read_app_logs(monitor)
        return monitor.report.result()

    assert asyncio.run(run()) == {"complete": False, "signal_name": "SIGTERM"}


def test_log_monitor_classifies_malformed_shutdown_marker_without_retaining_line() -> None:
    async def run() -> tuple[bool, bool, bool]:
        logs = asyncio.StreamReader()
        logs.feed_data(b"TETHER_QUALIFICATION_WORKER_STARTED current-call\n")
        logs.feed_data(b'TETHER_SHUTDOWN_REPORT "unparseable"\n')
        logs.feed_eof()
        monitor = AppLogMonitor(
            process=cast(asyncio.subprocess.Process, SimpleNamespace(stdout=logs)),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        await _read_app_logs(monitor)
        return (
            monitor.shutdown_marker_seen.is_set(),
            monitor.shutdown_marker_parse_failed,
            monitor.report.done(),
        )

    assert asyncio.run(run()) == (True, True, False)


def test_worker_readiness_waits_for_marker_without_startup_speed_deadline() -> None:
    async def run() -> None:
        logs = asyncio.StreamReader()
        monitor = AppLogMonitor(
            process=cast(asyncio.subprocess.Process, SimpleNamespace(stdout=logs, returncode=None)),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        monitor.reader = asyncio.create_task(_read_app_logs(monitor))
        logs.feed_data(b"TETHER_QUALIFICATION_WORKER_STARTED current-call\n")

        await monitor.wait_worker_started()

        assert not monitor.reader.done()
        logs.feed_eof()
        await monitor.reader

    asyncio.run(run())


def test_worker_readiness_ends_incomplete_when_follower_reaches_eof_first() -> None:
    async def run() -> None:
        logs = asyncio.StreamReader()
        logs.feed_eof()
        monitor = AppLogMonitor(
            process=cast(asyncio.subprocess.Process, SimpleNamespace(stdout=logs, returncode=0)),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        monitor.reader = asyncio.create_task(_read_app_logs(monitor))

        with pytest.raises(LiveQualificationIncomplete) as error:
            await monitor.wait_worker_started()

        assert error.value.reason == "live_worker_start_not_observed"

    asyncio.run(run())


def test_log_follow_watchdog_comes_only_from_explicit_observation_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    invocations: list[tuple[object, ...]] = []
    readers: list[asyncio.StreamReader] = []

    class Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            readers.append(self.stdout)

    async def create_subprocess_exec(*args: object, **_kwargs: object) -> Process:
        invocations.append(args)
        return Process()

    monkeypatch.setattr(lifecycle.shutil, "which", lambda _name: "/fake/databricks")
    monkeypatch.setattr(lifecycle.asyncio, "create_subprocess_exec", create_subprocess_exec)

    async def run() -> None:
        for policy in (LiveObservationPolicy(), LiveObservationPolicy(600)):
            with use_live_observation_policy(policy):
                monitor = await start_log_monitor(
                    "caller", None, {"PATH": "/bin"}, "fabricated-app", "current-call"
                )
            assert monitor.reader is not None
            readers[-1].feed_eof()
            await monitor.reader

    asyncio.run(run())
    assert "--timeout" not in invocations[0]
    assert invocations[1][invocations[1].index("--timeout") + 1] == "600s"


def test_shutdown_observation_is_incomplete_when_follower_eof_precedes_report() -> None:
    async def run() -> None:
        logs = asyncio.StreamReader()
        logs.feed_eof()
        monitor = AppLogMonitor(
            process=cast(
                asyncio.subprocess.Process,
                SimpleNamespace(stdout=logs, returncode=0),
            ),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        monitor.reader = asyncio.create_task(_read_app_logs(monitor))

        with pytest.raises(LiveQualificationIncomplete) as error:
            await monitor.wait_shutdown_report()

        assert error.value.reason == "live_shutdown_report_not_observed"

    asyncio.run(run())


def test_monitor_close_keeps_process_and_reader_cleanup_through_cancellation() -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.returncode: int | None = None
            self.terminated = asyncio.Event()
            self.released = asyncio.Event()
            self.killed = False

        def terminate(self) -> None:
            self.terminated.set()

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9
            self.stdout.feed_eof()
            self.released.set()

        async def wait(self) -> int:
            await self.released.wait()
            return self.returncode or 0

    async def run() -> tuple[bool, bool, bool]:
        process = Process()
        monitor = AppLogMonitor(
            process=cast(asyncio.subprocess.Process, process),
            invocation_id="current-call",
            worker_started=asyncio.Event(),
            report=asyncio.get_running_loop().create_future(),
        )
        monitor.reader = asyncio.create_task(_read_app_logs(monitor))
        closing = asyncio.create_task(monitor.aclose())
        await process.terminated.wait()
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        return process.killed, process.returncode == -9, monitor.reader.done()

    assert asyncio.run(run()) == (True, True, True)


def test_restart_failure_after_stop_restores_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_live_apps_lifecycle as lifecycle

    calls: list[tuple[str, ...]] = []

    async def admin_cli(
        profile: str,
        config_file: str | None,
        source: dict[str, str],
        *arguments: str,
    ) -> str:
        del profile, config_file, source
        calls.append(arguments)
        return "{}"

    async def wait_app_state(
        profile: str,
        config_file: str | None,
        source: dict[str, str],
        app: str,
        accepted: set[tuple[str, str]],
    ) -> None:
        del profile, config_file, source, app, accepted

    class MissingReport:
        async def wait_shutdown_report(self) -> dict[str, object]:
            raise RuntimeError("live_shutdown_report_missing")

    monkeypatch.setattr(lifecycle, "_admin_cli", admin_cli)
    monkeypatch.setattr(lifecycle, "_wait_app_state", wait_app_state)

    async def run() -> None:
        with pytest.raises(
            RuntimeError,
            match="qualification_restart_shutdown_report_live_shutdown_report_missing",
        ):
            await restart_for_unknown_effect(
                "fabricated-admin",
                None,
                {"TETHER_QUALIFICATION_APP": "fabricated-app"},
                cast(AppLogMonitor, MissingReport()),
            )

    asyncio.run(run())
    commands = [call[:2] for call in calls]
    if commands != [("apps", "stop"), ("apps", "start")]:
        pytest.fail("failed_restart_did_not_restore_app")


def test_browser_failure_cleanup_stops_its_process_group() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)",
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(0.1)
        await _stop_browser_group(process)
        assert process.returncode is not None

    asyncio.run(run())


def test_live_receipt_accepts_only_the_two_passed_selected_cases(tmp_path) -> None:
    report = tmp_path / "playwright.json"
    report.write_text(
        json.dumps(
            {
                "config": {"rootDir": str(ROOT)},
                "suites": [
                    {
                        "file": "tests/ui/databricks-live.spec.ts",
                        "specs": [
                            {
                                "title": title,
                                "tests": [
                                    {
                                        "expectedStatus": "passed",
                                        "results": [{"status": "passed", "duration": 50}],
                                    }
                                ],
                            }
                            for title in (
                                "@covers US-068-AC1 @covers US-068-AC5 "
                                "live browser, remote TUI and one approval winner",
                                "@covers US-068-AC8 live bounded stop retains unknown effect "
                                "barrier after restart",
                            )
                        ],
                    }
                ],
            }
        )
    )
    results = _browser_cases(report)

    assert len(results) == 2
    assert {item["outcome"] for item in results} == {"passed"}


def test_live_receipt_refuses_existing_or_source_tree_output(tmp_path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("stale receipt")
    with pytest.raises(RuntimeError, match="qualification_receipt_already_exists"):
        validate_receipt_output(existing)
    with pytest.raises(
        RuntimeError, match="qualification_receipt_path_must_be_external_or_ignored_artifact"
    ):
        validate_receipt_output(ROOT / "docs" / "stale.json")


def test_profile_binding_contains_no_cleartext_selection() -> None:
    digest = profile_binding_sha256("selected-profile", "https://apps.example")
    assert len(digest) == 64
    assert "selected-profile" not in digest
    assert "apps.example" not in digest


def test_live_failure_diagnostics_keep_only_bounded_routes_and_status(tmp_path) -> None:
    assert (
        _safe_http_event(
            {"method": "POST", "route": "turns", "status": 202, "path": "/private/session-id"}
        )
        == "turns-POST-202"
    )
    assert _safe_http_event({"method": "POST", "route": "/private/path", "status": 500}) is None

    async def run() -> str:
        (tmp_path / "browser-network-unknown.json").write_text(
            json.dumps(
                {
                    "requests": [
                        {"method": "POST", "route": "sessions", "status": 201},
                        {"method": "POST", "route": "turns", "status": 202},
                        {"method": "GET", "route": "pending", "status": 200},
                    ]
                }
            )
        )
        return await _safe_network_summary(tmp_path, "unknown")

    summary = asyncio.run(run())
    assert summary == "pending-GET-200-count-1,sessions-POST-201-count-1,turns-POST-202-count-1"


def test_live_failure_outcome_uses_only_known_case_and_status(tmp_path) -> None:
    path = tmp_path / "browser-outcome-shared.json"
    path.write_text(
        json.dumps(
            {
                "title": (
                    "@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI "
                    "and one approval winner"
                ),
                "status": "failed",
                "expected": "passed",
                "error_count": 1,
                "failure_category": "timeout",
                "failure_line": 120,
                "message": "content marker must not be copied",
            }
        )
    )

    assert asyncio.run(_safe_test_outcome(tmp_path, "shared")) == (
        "AC1-AC5-failed-passed-errors-1-timeout-line-120"
    )


def test_runtime_versions_are_bounded_safe_metadata() -> None:
    assert _runtime_version("Chromium 156.0") == "Chromium 156.0"
    with pytest.raises(RuntimeError, match="qualification_runtime_version_invalid"):
        _runtime_version("version /private/path")
