"""Databricks Apps launcher for an explicitly supplied runtime factory.

Set TETHER_APP_RUNTIME to ``module:function``. The function returns
``RuntimeBindings`` assembled from deployment settings and credential providers;
this file never reads or prints credential values.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import os
import platform
import signal
import sys
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from tether_databricks_example.runtime import RuntimeBindings, build_reference_app


class _ShutdownSignal:
    """Own launcher signals and measure from receipt, without logging context."""

    def __init__(self, on_signal: Callable[[float], None] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        self._previous = {}
        self._on_signal = on_signal
        self.received_at: float | None = None
        self.name: str | None = None

    def install(self) -> None:
        for name in ("SIGTERM", "SIGINT"):
            number = getattr(signal, name)
            self._previous[number] = signal.getsignal(number)
            self._loop.add_signal_handler(number, self._receive, name)

    def _receive(self, name: str) -> None:
        if self.received_at is None:
            self.received_at = self._loop.time()
            self.name = name
            if self._on_signal is not None:
                self._on_signal(self.received_at)
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def elapsed(self) -> float | None:
        return None if self.received_at is None else self._loop.time() - self.received_at

    def close(self) -> None:
        for number, previous in self._previous.items():
            self._loop.remove_signal_handler(number)
            signal.signal(number, previous)


def _factory(path: str) -> Callable[[], RuntimeBindings]:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError("TETHER_APP_RUNTIME_must_be_module_colon_factory")
    module = importlib.import_module(module_name)
    value = getattr(module, attribute, None)
    if not callable(value):
        raise RuntimeError("TETHER_APP_RUNTIME_factory_unavailable")
    return cast(Callable[[], RuntimeBindings], value)


async def main() -> None:
    # The deployed app entrypoint is the explicit environment boundary.
    factory_path = os.environ.get("TETHER_APP_RUNTIME")  # noqa: TID251
    if not factory_path:
        raise RuntimeError("TETHER_APP_RUNTIME_required")
    bindings = _factory(factory_path)()
    total_budget = bindings.settings.shutdown_timeout_seconds
    network_budget = min(0.25, total_budget / 10)
    report_budget = min(1.0, total_budget / 5)
    carrier_settings = bindings.settings.model_copy(
        update={"shutdown_timeout_seconds": total_budget - network_budget - report_budget}
    )
    app = await build_reference_app(
        replace(bindings, settings=carrier_settings),
        api_factory=bindings.api_factory,
        ui_factory=bindings.ui_factory,
    )
    config = Config()
    config.bind = [f"{bindings.settings.host}:{bindings.settings.port}"]
    # Hypercorn drains connections BEFORE ASGI lifespan shutdown. Giving both
    # phases the full budget would exceed Apps' signal allowance with live SSE.
    config.graceful_timeout = network_budget
    config.shutdown_timeout = total_budget - network_budget
    runtime_versions = _runtime_versions()
    shutdown = _ShutdownSignal(
        lambda received_at: app.limit_shutdown_deadline(received_at + total_budget - report_budget)
    )
    shutdown.install()
    try:
        await serve(cast(ASGIFramework, app), config, shutdown_trigger=shutdown.wait)
    finally:
        shutdown.close()
        # Metadata-only evidence of the carrier's bounded ASGI shutdown path.
        report = getattr(app, "shutdown_report", None)
        if report is not None:
            print(
                "TETHER_SHUTDOWN_REPORT "
                + json.dumps(
                    {
                        "complete": report.complete,
                        "closed": list(report.closed),
                        "outstanding": list(report.outstanding),
                        "asgi_http_versions": sorted(getattr(app, "asgi_http_versions", set())),
                        "runtime": runtime_versions,
                        "signal_name": shutdown.name,
                        "signal_to_report_seconds": shutdown.elapsed(),
                        "configured_shutdown_seconds": total_budget,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )


def _runtime_versions() -> dict[str, object]:
    distributions = (
        "novirhiza-tether",
        "novirhiza-tether-web-api",
        "novirhiza-tether-web-ui",
        "novirhiza-tether-databricks",
        "hypercorn",
        "databricks-sdk",
        "psycopg",
        "psycopg-binary",
    )
    versions: dict[str, str | None] = {}
    for name in distributions:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "architecture": platform.machine(),
        "distributions": versions,
    }


if __name__ == "__main__":
    asyncio.run(main())
