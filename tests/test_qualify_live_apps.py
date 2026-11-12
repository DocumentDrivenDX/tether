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


class _HeldLogProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.returncode: int | None = None
        self.terminated = asyncio.Event()
        self.killed = asyncio.Event()
        self.released = asyncio.Event()
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminated.set()

    def kill(self) -> None:
        self.killed.set()
        self.returncode = -9
        self.stdout.feed_eof()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self.released.wait()
        return self.returncode or 0


def _monitor_for_test(process: _HeldLogProcess) -> AppLogMonitor:
    monitor = AppLogMonitor(
        process=cast(asyncio.subprocess.Process, process),
        invocation_id="current-call",
        worker_started=asyncio.Event(),
        report=asyncio.get_running_loop().create_future(),
    )
    monitor.reader = asyncio.create_task(_read_app_logs(monitor))
    return monitor


def _track_monitor_close(
    monitor: AppLogMonitor,
    owners: list[asyncio.Task[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = monitor._aclose

    async def capture_close() -> None:
        owner = asyncio.current_task()
        assert owner is not None
        owners.append(owner)
        await original_close()

    monkeypatch.setattr(monitor, "_aclose", capture_close)


async def _monitor_close_control(monkeypatch: pytest.MonkeyPatch) -> tuple[bool, bool, bool, int]:
    import qualify_live_apps_lifecycle as lifecycle
    import selected_profiles_process
    from context_lifetime_controls import wait_event_or_task
    from process_lifetime_controls import ShieldCancellationObserver
    from selected_profiles_process import await_retained

    observer = ShieldCancellationObserver(2)
    observer.install(lifecycle, monkeypatch)
    observer.install(selected_profiles_process, monkeypatch)
    process = _HeldLogProcess()
    monitor = _monitor_for_test(process)
    close_owners: list[asyncio.Task[None]] = []
    _track_monitor_close(monitor, close_owners, monkeypatch)
    closing = asyncio.create_task(monitor.aclose())
    observer.target = closing
    try:
        await wait_event_or_task(process.terminated, closing)
        closing.cancel()
        await wait_event_or_task(observer.acknowledged[0], closing)
        await wait_event_or_task(process.killed, closing)
        assert len(close_owners) == 1
        assert not close_owners[0].done()
        closing.cancel()
        await wait_event_or_task(observer.acknowledged[1], closing)
        assert not closing.done()
        process.released.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await await_retained(close_owners[0])
        assert monitor.reader is not None
        return (
            process.killed.is_set(),
            process.returncode == -9,
            monitor.reader.done(),
            (process.wait_calls),
        )
    finally:
        process.released.set()
        if not closing.done():
            closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
        for owner in close_owners:
            if not owner.done():
                await await_retained(owner)


def test_monitor_close_keeps_process_and_reader_cleanup_through_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert asyncio.run(_monitor_close_control(monkeypatch)) == (True, True, True, 2)


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


def _browser_cleanup_fixture_code() -> str:
    descendant = (
        "import os,signal; print(f'DESCENDANT_READY {os.getpid()}', flush=True); signal.pause()"
    )
    return f"""\
import signal
import subprocess
import sys

child = None
def stop(*_args):
    if child is not None:
        if child.poll() is None:
            child.terminate()
        status = child.wait()
        print(f'DESCENDANT_REAPED {{child.pid}} {{status}}', flush=True)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
child = subprocess.Popen(
    [sys.executable, '-c', {descendant!r}], stdout=sys.stdout, stderr=sys.stderr
)
while True:
    signal.pause()
"""


async def _spawn_browser_cleanup_fixture() -> asyncio.subprocess.Process:
    from selected_profiles_process import await_retained

    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _browser_cleanup_fixture_code(),
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    try:
        return await await_retained(spawning)
    except BaseException as primary:
        if not spawning.done():
            try:
                await await_retained(spawning)
            except BaseException:
                raise primary from None
        try:
            process = spawning.result()
        except BaseException:
            raise primary from None
        cleanup = asyncio.create_task(_stop_browser_group(process))
        try:
            await await_retained(cleanup)
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "browser_fixture_spawn_and_cleanup_failed", [primary, cleanup_error]
            ) from primary
        raise


async def _browser_descendant_ready(
    process: asyncio.subprocess.Process, process_wait: asyncio.Task[int]
) -> int:
    if process.stdout is None:
        raise AssertionError("browser fixture stdout unavailable")
    readiness = asyncio.create_task(process.stdout.readline())
    try:
        done, _ = await asyncio.wait({readiness, process_wait}, return_when=asyncio.FIRST_COMPLETED)
        if readiness not in done:
            process_wait.result()
            raise AssertionError("browser descendant ended before readiness")
        fields = readiness.result().decode("utf-8").strip().split()
        assert len(fields) == 2
        assert fields[0] == "DESCENDANT_READY"
        return int(fields[1])
    finally:
        if not readiness.done():
            readiness.cancel()
            await asyncio.gather(readiness, return_exceptions=True)


async def _assert_browser_group_stopped(
    process: asyncio.subprocess.Process,
    descendant_pid: int,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    from selected_profiles_process import await_retained, wait_for_process_group_exit

    assert process.returncode is not None
    captured = await asyncio.gather(*output_tasks)
    reaped = f"DESCENDANT_REAPED {descendant_pid} ".encode()
    assert any(reaped in output for output, _truncated in captured)
    absence = asyncio.create_task(asyncio.to_thread(wait_for_process_group_exit, process.pid))
    await await_retained(absence)


def _browser_output_tasks(
    process: asyncio.subprocess.Process,
) -> tuple[asyncio.Task[tuple[bytes, bool]], ...]:
    from qualify_live_apps_browser import capture_output

    readers = tuple(reader for reader in (process.stdout, process.stderr) if reader is not None)
    return tuple(asyncio.create_task(capture_output(reader)) for reader in readers)


async def _finalize_browser_fixture(
    process: asyncio.subprocess.Process,
    process_wait: asyncio.Task[int],
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
    stopped: bool,
) -> None:
    errors: list[BaseException] = []
    if not stopped:
        try:
            await _stop_browser_group(process, output_tasks)
        except BaseException as error:
            errors.append(error)
    tasks = (process_wait, *output_tasks)
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("browser_fixture_cleanup_failed", errors)


def test_browser_failure_cleanup_stops_its_process_group() -> None:
    async def run() -> None:
        process = await _spawn_browser_cleanup_fixture()
        process_wait = asyncio.create_task(process.wait())
        output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...] = ()
        stopped = False
        primary: BaseException | None = None
        try:
            descendant_pid = await _browser_descendant_ready(process, process_wait)
            output_tasks = _browser_output_tasks(process)
            await _stop_browser_group(process, output_tasks)
            stopped = True
            await _assert_browser_group_stopped(process, descendant_pid, output_tasks)
        except BaseException as error:
            primary = error
            raise
        finally:
            cleanup = asyncio.create_task(
                _finalize_browser_fixture(process, process_wait, output_tasks, stopped)
            )
            try:
                from selected_profiles_process import await_retained

                await await_retained(cleanup)
            except BaseException as cleanup_error:
                if primary is None:
                    raise
                primary.add_note(f"browser fixture cleanup also failed: {cleanup_error!r}")

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
