"""Private browser and authenticated client setup for live qualification."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
from qualify_live_apps_browser import capture_output
from qualify_live_apps_config import LiveSettings
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_process import await_retained

from tether.client.databricks import (
    DatabricksCliProfile,
    DatabricksCliSettings,
    service_caller_auth,
)
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteProblem

ROOT = Path(__file__).resolve().parents[1]
_RESTART_RETRY_INITIAL_SECONDS = 0.25
_RESTART_RETRY_MAX_SECONDS = 2.0


@dataclass(frozen=True)
class LiveBrowserChild:
    process: asyncio.subprocess.Process
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...]


def _browser_environment(
    source: dict[str, str],
    settings: LiveSettings,
    authorization: str,
    rendezvous: Path,
    report: Path,
    service_caller: bool,
) -> dict[str, str]:
    private_output = rendezvous / "playwright-output"
    private_output.mkdir(mode=0o700)
    result = {
        name: source[name] for name in ("PATH", "HOME", "TMPDIR", "TEMP", "CI") if source.get(name)
    }
    result.update(
        {
            "TETHER_LIVE_APP_URL": settings.url,
            "TETHER_LIVE_MOUNT_PATH": settings.mount_path,
            "TETHER_LIVE_API_PREFIX": settings.mount_path + settings.api_prefix,
            "TETHER_LIVE_AUTHORIZATION": authorization,
            "TETHER_LIVE_RENDEZVOUS": str(rendezvous),
            "TETHER_LIVE_PRIVATE_OUTPUT": str(private_output),
            "TETHER_LIVE_JSON_REPORT": str(report),
        }
    )
    if service_caller:
        result["TETHER_LIVE_SERVICE_CALLER"] = "1"
    return result


async def _launch_browser(rendezvous: Path, environment: dict[str, str]) -> LiveBrowserChild:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm_unavailable")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            npm,
            "exec",
            "--",
            "playwright",
            "test",
            "--config",
            "tests/ui/databricks-live.config.ts",
            cwd=ROOT,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    )
    cancelled = False
    while not spawn.done():
        try:
            await asyncio.shield(spawn)
        except asyncio.CancelledError:
            cancelled = True
    process = spawn.result()
    readers = tuple(reader for reader in (process.stdout, process.stderr) if reader is not None)
    output_tasks = tuple(asyncio.create_task(capture_output(reader)) for reader in readers)
    child = LiveBrowserChild(process, output_tasks)
    if cancelled:
        await await_retained(asyncio.create_task(_stop_browser_group(process, output_tasks)))
        raise asyncio.CancelledError
    return child


async def _stop_browser_group(
    process: asyncio.subprocess.Process,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...] = (),
) -> None:
    """Stop the exact new session and settle its leader and bounded stream drains."""
    cleanup = asyncio.create_task(_stop_browser_group_owned(process, output_tasks))
    await await_retained(cleanup)


async def _stop_browser_group_owned(
    process: asyncio.subprocess.Process,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    output_settlement = asyncio.create_task(_settle_browser_output(process, output_tasks))
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    process_wait = asyncio.create_task(process.wait())
    await asyncio.wait({process_wait}, timeout=3)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process_wait
    await output_settlement


async def _settle_browser_output(
    process: asyncio.subprocess.Process,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    readers = tuple(reader for reader in (process.stdout, process.stderr) if reader is not None)
    pending: dict[asyncio.Task[object], asyncio.StreamReader | None] = {}
    for index, reader in enumerate(readers):
        if index < len(output_tasks):
            pending[cast(asyncio.Task[object], output_tasks[index])] = reader
        else:
            pending[cast(asyncio.Task[object], asyncio.create_task(_discard_output(reader)))] = None
    extra_tasks = output_tasks[len(readers) :]
    pending.update({cast(asyncio.Task[object], task): None for task in extra_tasks})
    failed_without_reader = False
    while pending:
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            reader = pending.pop(task)
            failed = task.cancelled() or task.exception() is not None
            if failed and reader is not None:
                discard = asyncio.create_task(_discard_output(reader))
                pending[cast(asyncio.Task[object], discard)] = None
            elif failed:
                failed_without_reader = True
    if failed_without_reader:
        raise LiveQualificationIncomplete("live_browser_output_not_settled")


async def _discard_output(reader: asyncio.StreamReader) -> None:
    while await reader.read(65536):
        pass


async def _client_for(
    settings: LiveSettings,
    token: DatabricksCliProfile,
    *,
    service_caller: bool,
) -> tuple[RemoteClient, httpx.AsyncClient]:
    async def credential_provider() -> DatabricksCliProfile:
        return token

    http = httpx.AsyncClient(
        base_url=settings.url,
        timeout=30,
        auth=service_caller_auth() if service_caller else None,
    )
    remote_settings = RemoteClientSettings(
        base_url=settings.url,
        api_prefix=settings.mount_path + settings.api_prefix,
        credential_provider=credential_provider,
        request_timeout_seconds=30,
    )
    try:
        client = await RemoteClient.open(remote_settings, http_client=http)
    except BaseException:
        await http.aclose()
        raise
    return client, http


async def _client_for_restart_readiness(
    settings: LiveSettings,
    token: DatabricksCliProfile,
    *,
    service_caller: bool,
) -> tuple[RemoteClient, httpx.AsyncClient]:
    """Retry safe capability reads until the restarted app is actually ready."""
    delay = _RESTART_RETRY_INITIAL_SECONDS
    while True:
        try:
            return await _client_for(settings, token, service_caller=service_caller)
        except RemoteProblem as error:
            if error.status not in {502, 503}:
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RESTART_RETRY_MAX_SECONDS)


async def _profile_credential(profile: str) -> tuple[DatabricksCliProfile, str]:
    token = DatabricksCliProfile(DatabricksCliSettings(profile=profile))
    authorization = await token.authorization()
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise RuntimeError("qualification_profile_credential_unavailable")
    return token, authorization
