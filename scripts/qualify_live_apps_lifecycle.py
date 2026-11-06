"""Private-profile controls for the live Apps restart qualification."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_observation import observation_watchdog_seconds, observe_until
from qualify_live_apps_process import capture_owned_command, spawn_owned_process
from selected_profiles_process import await_retained
from web_transport_receipt import validate_shutdown_observation

ROOT = Path(__file__).resolve().parents[1]


def _admin_environment(source: dict[str, str], config_file: str | None) -> dict[str, str]:
    safe_names = {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SSL_CERT_FILE"}
    environment = {key: value for key, value in source.items() if key in safe_names}
    if config_file:
        environment["DATABRICKS_CONFIG_FILE"] = config_file
    return environment


async def _admin_cli(
    profile: str,
    config_file: str | None,
    source: dict[str, str],
    *arguments: str,
) -> str:
    executable = shutil.which("databricks")
    if executable is None:
        raise RuntimeError("databricks_cli_unavailable")
    returncode, stdout = await capture_owned_command(
        executable,
        *arguments,
        "--profile",
        profile,
        "-o",
        "json",
        environment=_admin_environment(source, config_file),
        timeout_seconds=observation_watchdog_seconds(),
        incomplete_reason="live_admin_command_not_completed",
    )
    if returncode != 0:
        raise RuntimeError("qualification_admin_cli_failed")
    return stdout.decode("utf-8", errors="replace") if stdout is not None else ""


async def _wait_app_state(
    profile: str,
    config_file: str | None,
    source: dict[str, str],
    app: str,
    accepted: set[tuple[str, str]],
) -> None:
    async with observe_until("live_app_state_not_observed"):
        while True:
            value = json.loads(await _admin_cli(profile, config_file, source, "apps", "get", app))
            app_state = value.get("app_status", {}).get("state")
            compute_state = value.get("compute_status", {}).get("state")
            if (app_state, compute_state) in accepted:
                return
            await asyncio.sleep(3)


async def start_log_monitor(
    profile: str,
    config_file: str | None,
    source: dict[str, str],
    app: str,
    invocation_id: str,
) -> AppLogMonitor:
    executable = shutil.which("databricks")
    if executable is None:
        raise RuntimeError("databricks_cli_unavailable")
    arguments = [
        "apps",
        "logs",
        app,
        "--source",
        "APP",
        "--tail-lines",
        "100",
        "--follow",
        "--profile",
        profile,
    ]
    watchdog = observation_watchdog_seconds()
    if watchdog is not None:
        arguments.extend(("--timeout", f"{watchdog:g}s"))
    process = await spawn_owned_process(
        executable,
        *arguments,
        environment=_admin_environment(source, config_file),
    )
    loop = asyncio.get_running_loop()
    monitor = AppLogMonitor(
        process=process,
        invocation_id=invocation_id,
        worker_started=asyncio.Event(),
        report=loop.create_future(),
    )
    monitor.reader = asyncio.create_task(_read_app_logs(monitor))
    return monitor


def qualification_app(source: dict[str, str]) -> str:
    app = source.get("TETHER_QUALIFICATION_APP")
    if not app:
        raise RuntimeError("qualification_app_name_missing")
    return app


@dataclass
class AppLogMonitor:
    process: asyncio.subprocess.Process
    invocation_id: str
    worker_started: asyncio.Event
    report: asyncio.Future[dict[str, Any]]
    reader: asyncio.Task[None] | None = None
    discard_reader: asyncio.Task[None] | None = None
    shutdown_marker_seen: asyncio.Event = field(default_factory=asyncio.Event)
    shutdown_marker_parse_failed: bool = False

    async def wait_worker_started(self) -> None:
        if self.reader is None:
            raise RuntimeError("qualification_log_reader_unavailable")
        started = asyncio.create_task(self.worker_started.wait())
        try:
            async with observe_until("live_worker_start_not_observed"):
                done, _ = await asyncio.wait(
                    {started, self.reader}, return_when=asyncio.FIRST_COMPLETED
                )
                if started in done and self.worker_started.is_set():
                    return
                try:
                    await self.reader
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise LiveQualificationIncomplete("live_worker_start_not_observed") from None
                raise LiveQualificationIncomplete("live_worker_start_not_observed")
        finally:
            if not started.done():
                started.cancel()
                await asyncio.gather(started, return_exceptions=True)

    async def wait_shutdown_report(self) -> dict[str, Any]:
        if self.reader is None:
            raise LiveQualificationIncomplete("live_shutdown_report_not_observed")
        report_wait = asyncio.ensure_future(asyncio.shield(self.report))
        try:
            async with observe_until("live_shutdown_report_not_observed"):
                done, _ = await asyncio.wait(
                    {report_wait, self.reader}, return_when=asyncio.FIRST_COMPLETED
                )
                if report_wait in done:
                    return report_wait.result()
                try:
                    await self.reader
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise LiveQualificationIncomplete("live_shutdown_report_not_observed") from None
                raise LiveQualificationIncomplete("live_shutdown_report_not_observed")
        finally:
            if not report_wait.done():
                report_wait.cancel()
                await asyncio.gather(report_wait, return_exceptions=True)

    async def aclose(self) -> None:
        cleanup = asyncio.create_task(self._aclose())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await await_retained(asyncio.create_task(self._force_close()))
            with contextlib.suppress(Exception):
                await await_retained(cleanup)
            raise

    async def _aclose(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
        incomplete = False
        try:
            async with observe_until("live_log_follower_cleanup_not_observed"):
                await self._settle_owned()
        except LiveQualificationIncomplete:
            incomplete = True
            if self.process.returncode is None:
                self.process.kill()
            await await_retained(asyncio.create_task(self._settle_after_kill()))
        if incomplete:
            raise LiveQualificationIncomplete("live_log_follower_cleanup_not_observed")

    async def _settle_owned(self) -> None:
        process_wait = asyncio.create_task(self.process.wait())
        try:
            await self._wait_for_process_and_reader(process_wait)
        except BaseException:
            if self.process.returncode is None:
                self.process.kill()
            failed_reader = self.reader
            if (
                failed_reader is not None
                and failed_reader.done()
                and (failed_reader.cancelled() or failed_reader.exception() is not None)
            ):
                self._start_discard_reader()
            await await_retained(asyncio.create_task(self._settle_after_kill()))
            raise
        finally:
            if not process_wait.done():
                await await_retained(process_wait)

    async def _wait_for_process_and_reader(self, process_wait: asyncio.Task[int]) -> None:
        reader = self.reader
        if reader is None:
            raise LiveQualificationIncomplete("live_log_follower_cleanup_not_observed")
        while not process_wait.done() and reader is not None:
            done, _ = await asyncio.wait(
                {process_wait, reader}, return_when=asyncio.FIRST_COMPLETED
            )
            if process_wait in done:
                break
            if reader.cancelled() or reader.exception() is not None:
                self._start_discard_reader()
                raise LiveQualificationIncomplete("live_log_follower_cleanup_not_observed")
            await reader
            reader = None
        await process_wait
        if reader is not None and not reader.done():
            await reader
        if reader is not None and (reader.cancelled() or reader.exception() is not None):
            await self._start_discard_reader()
            raise LiveQualificationIncomplete("live_log_follower_cleanup_not_observed")

    def _start_discard_reader(self) -> asyncio.Task[None]:
        if self.discard_reader is not None:
            return self.discard_reader
        stdout = self.process.stdout
        if stdout is None:
            raise LiveQualificationIncomplete("live_log_follower_cleanup_not_observed")
        self.discard_reader = asyncio.create_task(_discard_stream(stdout))
        return self.discard_reader

    async def _force_close(self) -> None:
        if self.process.returncode is None:
            self.process.kill()
        reader = self.reader
        if (
            reader is not None
            and reader.done()
            and (reader.cancelled() or reader.exception() is not None)
        ):
            self._start_discard_reader()
        await await_retained(asyncio.create_task(self._settle_after_kill()))

    async def _settle_after_kill(self) -> None:
        process_wait = asyncio.create_task(self.process.wait())
        tasks: set[asyncio.Task[object]] = {process_wait}
        if self.discard_reader is not None:
            tasks.add(self.discard_reader)
        elif self.reader is not None:
            tasks.add(self.reader)
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is process_wait:
                    continue
                if self.discard_reader is None and (
                    task.cancelled() or task.exception() is not None
                ):
                    tasks.add(self._start_discard_reader())
                tasks.discard(task)
            tasks = pending
            if self.discard_reader is not None and not self.discard_reader.done():
                tasks.add(self.discard_reader)
        await process_wait


async def _discard_stream(stream: asyncio.StreamReader) -> None:
    while await stream.read(65536):
        pass


async def _read_app_logs(monitor: AppLogMonitor) -> None:
    stdout = monitor.process.stdout
    if stdout is None:
        raise RuntimeError("qualification_log_stream_unavailable")
    marker = f"TETHER_QUALIFICATION_WORKER_STARTED {monitor.invocation_id}"
    async for raw_line in stdout:
        line = raw_line.decode("utf-8", errors="replace")
        if marker in line:
            monitor.worker_started.set()
        report_marker = "TETHER_SHUTDOWN_REPORT "
        if monitor.worker_started.is_set() and report_marker in line and not monitor.report.done():
            monitor.shutdown_marker_seen.set()
            try:
                value = shutdown_report(line)
            except RuntimeError:
                monitor.shutdown_marker_parse_failed = True
                continue
            monitor.report.set_result(value)


async def restart_for_unknown_effect(
    profile: str,
    config_file: str | None,
    source: dict[str, str],
    monitor: AppLogMonitor,
) -> dict[str, Any]:
    app = qualification_app(source)
    phase = "stop_command"
    stopped = False
    try:
        await _admin_cli(
            profile,
            config_file,
            source,
            "apps",
            "stop",
            app,
            "--no-wait",
        )
        phase = "wait_stopped"
        await _wait_app_state(
            profile,
            config_file,
            source,
            app,
            {("STOPPED", "STOPPED"), ("UNAVAILABLE", "STOPPED")},
        )
        stopped = True
        phase = "shutdown_report"
        report = await monitor.wait_shutdown_report()
        try:
            await asyncio.to_thread(
                validate_shutdown_observation,
                ROOT,
                report,
                invocation_id=monitor.invocation_id,
            )
        except ValueError:
            raise RuntimeError("live_apps_shutdown_observation_not_qualified") from None
        report["worker_invocation_id"] = monitor.invocation_id
        phase = "start_command"
        await _admin_cli(
            profile,
            config_file,
            source,
            "apps",
            "start",
            app,
            "--no-wait",
        )
        phase = "wait_running"
        await _wait_app_state(
            profile,
            config_file,
            source,
            app,
            {("RUNNING", "ACTIVE")},
        )
        stopped = False
        return report
    except LiveQualificationIncomplete:
        if stopped:
            try:
                await _admin_cli(
                    profile,
                    config_file,
                    source,
                    "apps",
                    "start",
                    app,
                    "--no-wait",
                )
                await _wait_app_state(
                    profile,
                    config_file,
                    source,
                    app,
                    {("RUNNING", "ACTIVE")},
                )
            except Exception:
                raise LiveQualificationIncomplete("live_app_state_not_observed") from None
        raise
    except Exception as error:
        code = str(error) if type(error) is RuntimeError else type(error).__name__
        if stopped:
            try:
                await _admin_cli(
                    profile,
                    config_file,
                    source,
                    "apps",
                    "start",
                    app,
                    "--no-wait",
                )
                await _wait_app_state(
                    profile,
                    config_file,
                    source,
                    app,
                    {("RUNNING", "ACTIVE")},
                )
            except Exception:
                raise RuntimeError(
                    f"qualification_restart_{phase}_{code}_recovery_start_failed"
                ) from None
        raise RuntimeError(f"qualification_restart_{phase}_{code}") from None


def shutdown_report(logs: str) -> dict[str, Any]:
    for line in reversed(logs.splitlines()):
        marker = "TETHER_SHUTDOWN_REPORT "
        if marker in line:
            try:
                value = json.loads(line.split(marker, 1)[1])
            except ValueError:
                break
            if isinstance(value, dict):
                return value
    raise RuntimeError("live_shutdown_report_missing")
