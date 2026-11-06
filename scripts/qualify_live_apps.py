"""Run the live browser/remote-TUI qualification without retaining credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from qualify_live_apps_browser import LiveBrowserFailure, browser_failure
from qualify_live_apps_cleanup import cleanup_case_resources, cleanup_unknown_effect_resources
from qualify_live_apps_config import LiveSettings, settings_from_environment
from qualify_live_apps_diagnostics import read_rendezvous
from qualify_live_apps_evidence import validate_receipt_output, write_live_receipt
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_lifecycle import (
    AppLogMonitor,
    qualification_app,
    restart_for_unknown_effect,
    start_log_monitor,
)
from qualify_live_apps_observation import (
    LiveObservationPolicy,
    observe_until,
    use_live_observation_policy,
)
from qualify_live_apps_transport import (
    _browser_environment,
    _client_for,
    _client_for_restart_readiness,
    _launch_browser,
    _profile_credential,
)
from qualify_live_apps_transport import (
    _stop_browser_group as _stop_browser_group,
)
from selected_profiles_process import await_retained
from textual.widgets import Button
from web_artifacts import inventory

from tether.client import Answer, ExecutionDenied
from tether.client.databricks import (
    DatabricksCliProfile,
)
from tether.client.remote import RemoteClient, RemoteProblem
from tether.tui import ClientApp

ROOT = Path(__file__).resolve().parents[1]


_read_rendezvous = read_rendezvous


async def _wait_result(client: Any, session_id: str, turn_id: str) -> Any:
    async with observe_until("live_turn_terminal_not_observed"):
        while True:
            result = await client.turn_result(session_id, turn_id)
            if result is not None:
                return result
            await asyncio.sleep(0.1)


async def _verify_tui(client: Any, session_id: str) -> None:
    app = ClientApp(client, session_id=session_id)
    try:
        async with observe_until("live_tui_readiness_not_observed"), app.run_test() as pilot:
            if app.session_id != session_id:
                raise RuntimeError("live_tui_selected_session_mismatch")
            while app.query_one("#approve", Button).disabled:
                await pilot.pause()
    except LiveQualificationIncomplete:
        raise


async def _validate_shared_session(
    client: RemoteClient, session_id: str, invocation_id: str
) -> None:
    view = await client.get(session_id)
    pending = await client.pending(session_id)
    if view.summary.id != session_id or pending is None:
        raise RuntimeError("live_remote_shared_session_unavailable")
    if pending.invocation_id != invocation_id:
        raise RuntimeError("live_remote_pending_identity_mismatch")
    await _verify_tui(client, session_id)


async def _race_approval(
    client: RemoteClient,
    session_id: str,
    invocation_id: str,
    rendezvous: Path,
    process: asyncio.subprocess.Process,
) -> bool:
    gate = rendezvous / "race-approval"
    await _write_gate(gate)
    await _read_rendezvous(rendezvous, "browser-request-ready.json", process=process)
    approval = asyncio.create_task(client.confirm(session_id, invocation_id, Answer(approved=True)))
    await asyncio.sleep(0)
    await _write_gate(rendezvous / "release-browser-approval")
    winner = await approval
    accepted_turn = winner
    if accepted_turn is None:
        browser = await _read_rendezvous(rendezvous, "browser-accepted.json", process=process)
        candidate = browser.get("turn_id")
        if not isinstance(candidate, str) or not candidate:
            raise RuntimeError("live_browser_turn_id_missing")
        result = await _wait_result(client, session_id, candidate)
    else:
        async with observe_until("live_turn_terminal_not_observed"):
            result = await accepted_turn.result()
    if result.stop_reason != "final":
        raise RuntimeError("live_competing_approval_not_final")
    tool_results = [message for message in result.messages if message.role == "tool"]
    if len(tool_results) != 1:
        raise RuntimeError("live_competing_approval_effect_count_invalid")
    if tool_results[0].content != "Fabricated Tether qualification effect completed.":
        raise RuntimeError("live_competing_approval_result_invalid")
    return winner is not None


async def _qualify_shared_session(
    client: RemoteClient, process: asyncio.subprocess.Process, rendezvous: Path
) -> None:
    accepted = await _read_rendezvous(rendezvous, "pending.json", process=process)
    session_id = accepted.get("session_id")
    invocation_id = accepted.get("invocation_id")
    if not isinstance(session_id, str) or not isinstance(invocation_id, str):
        raise RuntimeError("live_browser_rendezvous_invalid")
    await _validate_shared_session(client, session_id, invocation_id)
    remote_won = await _race_approval(client, session_id, invocation_id, rendezvous, process)
    browser_status = await _validate_browser_approval(rendezvous, process)
    if int(remote_won) + int(browser_status == 202) != 1:
        raise RuntimeError("live_competing_approval_winner_count_invalid")
    print("US-068-AC1 shared browser and remote TUI session: passed")
    print("US-068-AC5 competing approval and reconnect: passed")


async def _qualify_unknown_effect(
    client: RemoteClient,
    http: httpx.AsyncClient,
    profile_token: DatabricksCliProfile,
    admin_profile: str,
    admin_config_file: str | None,
    source: dict[str, str],
    settings: LiveSettings,
    service_caller: bool,
    browser: asyncio.subprocess.Process,
    rendezvous: Path,
) -> tuple[RemoteClient, httpx.AsyncClient, AppLogMonitor, dict[str, Any]]:
    phase = ["pending_rendezvous"]
    monitor: AppLogMonitor | None = None
    try:
        pending, monitor = await _prepare_unknown_effect(
            client,
            admin_profile,
            admin_config_file,
            source,
            browser,
            rendezvous,
            phase,
        )
        shutdown = await _restart_unknown_effect(
            admin_profile, admin_config_file, source, monitor, phase
        )
        client, http = await _reopen_after_unknown_effect(
            client,
            http,
            settings,
            profile_token,
            service_caller,
            pending,
            browser,
            rendezvous,
            phase,
        )
        return client, http, monitor, shutdown
    except LiveQualificationIncomplete:
        await cleanup_unknown_effect_resources(client, http, monitor)
        raise
    except RemoteProblem:
        await cleanup_unknown_effect_resources(client, http, monitor)
        raise
    except Exception as error:
        await cleanup_unknown_effect_resources(client, http, monitor)
        code = str(error) if type(error) is RuntimeError else type(error).__name__
        raise RuntimeError(f"live_unknown_effect_{phase[0]}_{code}") from None
    except BaseException:
        await cleanup_unknown_effect_resources(client, http, monitor)
        raise


async def _prepare_unknown_effect(
    client: RemoteClient,
    admin_profile: str,
    admin_config_file: str | None,
    source: dict[str, str],
    browser: asyncio.subprocess.Process,
    rendezvous: Path,
    phase: list[str],
) -> tuple[dict[str, Any], AppLogMonitor]:
    pending = await _read_rendezvous(rendezvous, "unknown-pending.json", process=browser)
    invocation_id = pending.get("invocation_id")
    if not isinstance(invocation_id, str):
        raise RuntimeError("live_unknown_effect_rendezvous_invalid")
    phase[0] = "start_app_log_monitor"
    monitor = await start_log_monitor(
        admin_profile,
        admin_config_file,
        source,
        qualification_app(source),
        invocation_id,
    )
    phase[0] = "approve_effect"
    await _approve_unknown_from_browser(rendezvous)
    phase[0] = "accepted_rendezvous"
    accepted = await _read_rendezvous(rendezvous, "unknown-accepted.json", process=browser)
    pending.update(accepted)
    phase[0] = "verify_started_effect"
    await _validate_started_effect(client, pending)
    phase[0] = "observe_worker_start"
    await monitor.wait_worker_started()
    return pending, monitor


async def _restart_unknown_effect(
    admin_profile: str,
    admin_config_file: str | None,
    source: dict[str, str],
    monitor: AppLogMonitor,
    phase: list[str],
) -> dict[str, Any]:
    phase[0] = "restart_app"
    return await restart_for_unknown_effect(admin_profile, admin_config_file, source, monitor)


async def _reopen_after_unknown_effect(
    client: RemoteClient,
    http: httpx.AsyncClient,
    settings: LiveSettings,
    profile_token: DatabricksCliProfile,
    service_caller: bool,
    pending: dict[str, Any],
    browser: asyncio.subprocess.Process,
    rendezvous: Path,
    phase: list[str],
) -> tuple[RemoteClient, httpx.AsyncClient]:
    await client.aclose()
    await http.aclose()
    phase[0] = "reopen_remote_client"
    client, http = await _client_for_restart_readiness(
        settings, profile_token, service_caller=service_caller
    )
    phase[0] = "verify_recovery_barrier"
    await _validate_recovered_barrier(client, pending)
    phase[0] = "browser_recovery"
    await _write_gate(rendezvous / "restart-complete")
    await _validate_unknown_browser_finish(browser, rendezvous)
    return client, http


async def _run_case(
    profile: str,
    admin_profile: str,
    admin_config_file: str | None,
    source: dict[str, str],
    settings: LiveSettings,
    output: Path,
    service_caller: bool,
) -> None:
    source_before = await asyncio.to_thread(inventory, ROOT)
    profile_token, authorization = await _profile_credential(profile)
    with TemporaryDirectory(prefix="tether-live-browser-") as temporary:
        rendezvous = Path(temporary)
        await asyncio.to_thread(rendezvous.chmod, 0o700)
        private_output = rendezvous / "playwright-output"
        report_path = private_output / "report.json"
        environment = _browser_environment(
            source, settings, authorization, rendezvous, report_path, service_caller
        )
        client, http = await _client_for(settings, profile_token, service_caller=service_caller)
        process: asyncio.subprocess.Process | None = None
        output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...] = ()
        monitor = None
        shutdown_result: dict[str, Any] | None = None
        try:
            browser_child = await _launch_browser(rendezvous, environment)
            browser_process = browser_child.process
            process = browser_process
            if browser_process.stdout is None or browser_process.stderr is None:
                raise RuntimeError("live_browser_output_unavailable")
            output_tasks = browser_child.output_tasks
            await _qualify_shared_session(client, browser_process, rendezvous)
            client, http, monitor, shutdown_result = await _qualify_unknown_effect(
                client,
                http,
                profile_token,
                admin_profile,
                admin_config_file,
                source,
                settings,
                service_caller,
                browser_process,
                rendezvous,
            )
        except LiveQualificationIncomplete:
            raise
        except RuntimeError:
            if process is None or process.returncode is None:
                raise
            raise await browser_failure(process, report_path, output_tasks) from None
        finally:
            cleanup = asyncio.create_task(
                cleanup_case_resources(client, http, monitor, process, output_tasks)
            )
            await await_retained(cleanup)
        if shutdown_result is not None:
            await write_live_receipt(
                output,
                source_before,
                source,
                report_path,
                rendezvous / "browser-runtime.json",
                shutdown_result,
                profile_name=profile,
                app_origin=settings.url,
                service_caller=service_caller,
            )
            print("US-068-AC8 bounded stop, restart and unknown-effect barrier: passed")


async def _validate_browser_approval(rendezvous: Path, process: asyncio.subprocess.Process) -> int:
    finished = await _read_rendezvous(rendezvous, "browser-finished.json", process=process)
    if finished.get("approval_status") not in {200, 202}:
        raise RuntimeError("live_browser_approval_not_observed")
    return int(finished["approval_status"])


async def _validate_unknown_browser_finish(
    process: asyncio.subprocess.Process, rendezvous: Path
) -> None:
    finished = await _read_rendezvous(rendezvous, "unknown-finished.json", process=process)
    if finished.get("stop_reason") not in {"cancelled", "interrupted"}:
        raise RuntimeError("live_browser_recovery_terminal_status_invalid")
    async with observe_until("live_browser_exit_not_observed"):
        await process.wait()
    if process.returncode != 0:
        raise RuntimeError("live_browser_case_failed")


async def _write_gate(path: Path) -> None:
    await asyncio.to_thread(path.write_text, "go\n")
    await asyncio.to_thread(path.chmod, 0o600)


async def _approve_unknown_from_browser(rendezvous: Path) -> None:
    await _write_gate(rendezvous / "approve-unknown-effect")


async def _validate_started_effect(client: RemoteClient, item: dict[str, Any]) -> None:
    session_id = item.get("session_id")
    invocation_id = item.get("invocation_id")
    turn_id = item.get("turn_id")
    if (
        not isinstance(session_id, str)
        or not isinstance(invocation_id, str)
        or not isinstance(turn_id, str)
    ):
        raise RuntimeError("live_unknown_effect_rendezvous_invalid")
    async with observe_until("live_unknown_effect_intent_not_durable"):
        while True:
            page = await client.effects(session_id)
            if any(effect.invocation_id == invocation_id for effect in page.items):
                if await client.turn_result(session_id, turn_id) is not None:
                    raise RuntimeError("live_unknown_effect_completed_before_restart")
                return
            await asyncio.sleep(0.1)


async def _validate_recovered_barrier(client: RemoteClient, item: dict[str, Any]) -> None:
    session_id = item.get("session_id")
    invocation_id = item.get("invocation_id")
    turn_id = item.get("turn_id")
    if (
        not isinstance(session_id, str)
        or not isinstance(invocation_id, str)
        or not isinstance(turn_id, str)
    ):
        raise RuntimeError("live_unknown_effect_rendezvous_invalid")
    view = await client.get(session_id)
    if view.summary.id != session_id:
        raise RuntimeError("live_recovered_session_read_failed")
    async with observe_until("live_unknown_effect_recovery_not_visible"):
        while True:
            result = await client.turn_result(session_id, turn_id)
            effects = await client.effects(session_id)
            matching = [effect for effect in effects.items if effect.invocation_id == invocation_id]
            if (
                result is not None
                and result.stop_reason in {"cancelled", "interrupted"}
                and len(matching) == 1
                and matching[0].reason == "outcome_unknown"
            ):
                break
            await asyncio.sleep(0.5)
    try:
        await client.close(session_id)
    except ExecutionDenied as error:
        if error.reason == "denied":
            return
    raise RuntimeError("live_unknown_effect_barrier_not_enforced")


async def _run(
    profile: str,
    admin_profile: str,
    admin_config_file: str | None,
    source: dict[str, str],
    output: Path,
    service_caller: bool,
) -> None:
    validate_receipt_output(output)
    await _run_case(
        profile,
        admin_profile,
        admin_config_file,
        source,
        settings_from_environment(source),
        output,
        service_caller,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--admin-profile", required=True)
    parser.add_argument("--admin-config-file", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service-caller", action="store_true")
    parser.add_argument(
        "--observation-watchdog-seconds",
        type=float,
        default=None,
        help="Optional per-observation emergency limit; exhaustion reports incomplete.",
    )
    args = parser.parse_args()
    source = dict(os.environ)  # noqa: TID251
    try:
        policy = LiveObservationPolicy(args.observation_watchdog_seconds)
        with use_live_observation_policy(policy):
            asyncio.run(
                _run(
                    args.profile,
                    args.admin_profile,
                    args.admin_config_file,
                    source,
                    args.output,
                    args.service_caller,
                )
            )
    except RemoteProblem as error:
        print(f"Live Apps check failed: HTTP {error.status} {error.code}", file=sys.stderr)
        return 1
    except LiveBrowserFailure as error:
        print(
            "Live Apps browser failure: " + json.dumps(error.summary, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    except LiveQualificationIncomplete as error:
        print(f"Live Apps check incomplete: {error.reason}", file=sys.stderr)
        return 2
    except Exception as error:
        safe = str(error) if type(error) is RuntimeError else type(error).__name__
        print(f"Live Apps check failed: {safe}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
