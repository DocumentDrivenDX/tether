"""Retain all resources owned by one live Apps qualification case."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import httpx
from qualify_live_apps_lifecycle import AppLogMonitor
from qualify_live_apps_transport import _stop_browser_group
from selected_profiles_process import await_retained

from tether.client.remote import RemoteClient


async def cleanup_case_resources(
    client: RemoteClient,
    http: httpx.AsyncClient,
    monitor: AppLogMonitor | None,
    process: asyncio.subprocess.Process | None,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> None:
    operations: list[Awaitable[None]] = []
    if process is not None:
        operations.append(_stop_browser_group(process, output_tasks))
    operations.extend((client.aclose(), http.aclose()))
    if monitor is not None:
        operations.append(monitor.aclose())
    results = await asyncio.gather(*operations, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def cleanup_unknown_effect_resources(
    client: RemoteClient, http: httpx.AsyncClient, monitor: AppLogMonitor | None
) -> None:
    cleanup = asyncio.create_task(cleanup_case_resources(client, http, monitor, None, ()))
    await await_retained(cleanup)


__all__ = ["cleanup_case_resources", "cleanup_unknown_effect_resources"]
