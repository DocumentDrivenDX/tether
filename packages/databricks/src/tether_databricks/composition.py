"""One-host API and UI composition with explicit identity and lifecycle seams."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import cast

from tether_web_api import ApiFactory, ApiSettings, Authenticator
from tether_web_ui import ASGIApp, UiFactory, UiSettings

from tether.client import ClientProvider

from .identity import DatabricksIdentityResolver
from .lifecycle import CarrierLifecycle, ShutdownReport
from .resources import DatabricksResources, ResourceProvider
from .settings import DatabricksAppSettings

Scope = Mapping[str, object]
Message = dict[str, object]
Receive = Callable[[], Awaitable[Mapping[str, object]]]
Send = Callable[[Message], Awaitable[None]]


class _Lifespan:
    def __init__(self, app: ASGIApp, name: str) -> None:
        self.app, self.name = app, name
        self.incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.outgoing: asyncio.Queue[Message] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self._shutdown_sent = False

    async def start(self) -> None:
        async def receive() -> Message:
            return await self.incoming.get()

        async def send(message: Message) -> None:
            await self.outgoing.put(message)

        self.task = asyncio.create_task(
            self.app(
                {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}},
                receive,
                send,
            ),
            name=f"tether-carrier-{self.name}-lifespan",
        )
        await self.incoming.put({"type": "lifespan.startup"})
        response = asyncio.create_task(self.outgoing.get())
        try:
            await asyncio.wait((response, self.task), return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():
                if not self.task.cancelled():
                    self.task.exception()
                raise RuntimeError(f"{self.name}_startup_failed")
            message = response.result()
        finally:
            response.cancel()
            await asyncio.gather(response, return_exceptions=True)
        if message.get("type") != "lifespan.startup.complete":
            raise RuntimeError(f"{self.name}_startup_failed")

    async def stop(self, deadline_seconds: float) -> bool:
        if self.task is None:
            return True
        if self.task.done():
            return not self.task.cancelled() and self.task.exception() is None
        if not self._shutdown_sent:
            self._shutdown_sent = True
            await self.incoming.put({"type": "lifespan.shutdown"})
        if deadline_seconds <= 0:
            return False
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=deadline_seconds)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if self.task.cancelled() and (current is None or not current.cancelling()):
                return False
            raise
        except Exception:
            return False
        return self.task.exception() is None


class CarrierASGI:
    """Route HTTP traffic by API prefix and retain child shutdown ownership."""

    def __init__(
        self,
        *,
        api: ASGIApp,
        ui: ASGIApp,
        api_prefix: str,
        shutdown_timeout_seconds: float,
        lifecycle: CarrierLifecycle,
        resources: ResourceProvider,
    ) -> None:
        self._api = api
        self._ui = ui
        self._api_prefix = api_prefix.rstrip("/")
        self._shutdown_timeout = shutdown_timeout_seconds
        self._shutdown_deadline: float | None = None
        self._shutdown_started = False
        self._lifecycle = lifecycle
        self.resource_provider = resources
        self.shutdown_report: ShutdownReport | None = None
        self.asgi_http_versions: set[str] = set()
        self._close_task: asyncio.Task[ShutdownReport] | None = None
        self._startup_cleanup_task: asyncio.Task[None] | None = None
        self._lifespans: tuple[_Lifespan, ...] = ()

    def limit_shutdown_deadline(self, deadline: float) -> None:
        """Tighten shutdown to an absolute time on the owning event-loop clock.

        A launcher can include connection draining in its signal allowance.
        Call before ASGI shutdown begins. Repeated calls never extend a
        previously declared deadline.
        """
        if self._shutdown_started:
            raise RuntimeError("carrier_shutdown_started")
        if not math.isfinite(deadline):
            raise ValueError("invalid_shutdown_deadline")
        self._shutdown_deadline = (
            deadline if self._shutdown_deadline is None else min(self._shutdown_deadline, deadline)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._serve_lifespan(receive, send)
            return
        if scope_type != "http":
            raise ValueError("unsupported_asgi_scope")
        http_version = scope.get("http_version")
        if isinstance(http_version, str):
            self.asgi_http_versions.add(http_version)
        path = scope.get("path")
        if not isinstance(path, str):
            raise ValueError("invalid_asgi_path")
        api_path = path == self._api_prefix or path.startswith(self._api_prefix + "/")
        target = self._api if api_path else self._ui
        await target(scope, receive, send)

    async def _serve_lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            kind = message.get("type")
            if kind == "lifespan.startup":
                api = _Lifespan(self._api, "api")
                ui = _Lifespan(self._ui, "ui")
                self._lifespans = (api, ui)
                try:
                    await api.start()
                    await ui.start()
                except BaseException as error:

                    async def discard(_message: Message) -> None:
                        pass

                    self._startup_cleanup_task = asyncio.create_task(self._shutdown(discard))
                    await asyncio.shield(self._startup_cleanup_task)
                    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    await send(
                        {"type": "lifespan.startup.failed", "message": "Carrier startup failed"}
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif kind == "lifespan.shutdown":
                await self._shutdown(send)
                return

    async def _shutdown(self, send: Send) -> None:
        self._shutdown_started = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout
        if self._shutdown_deadline is not None:
            deadline = min(deadline, self._shutdown_deadline)
        closed: list[str] = []
        outstanding: list[str] = []
        for name, lifespan in reversed(tuple(zip(("api", "ui"), self._lifespans, strict=True))):
            if await lifespan.stop(max(0.0, deadline - loop.time())):
                closed.append(name)
            else:
                outstanding.append(name)
        if self._close_task is None:
            remaining = max(0.001, deadline - loop.time())
            # The injected lifecycle needs time to return its named resource
            # report before the outer watchdog expires. Equal nested relative
            # deadlines race because the inner timer starts on the next turn.
            report_headroom = min(0.05, remaining / 10)
            self._close_task = asyncio.create_task(
                self._lifecycle.close(timeout_seconds=remaining - report_headroom),
                name="tether-carrier-resource-shutdown",
            )
        remaining = max(0.0, deadline - loop.time())
        try:
            report = await asyncio.wait_for(asyncio.shield(self._close_task), timeout=remaining)
            closed.extend(report.closed)
            outstanding.extend(report.outstanding)
            if not report.complete and not report.outstanding:
                outstanding.append("carrier_lifecycle")
        except TimeoutError:
            outstanding.append("carrier_lifecycle")
        except Exception:
            outstanding.append("carrier_lifecycle")
        self.shutdown_report = ShutdownReport(
            complete=not outstanding,
            outstanding=tuple(sorted(set(outstanding))),
            closed=tuple(sorted(set(closed))),
        )
        await send({"type": "lifespan.shutdown.complete"})


def create_carrier_app(
    *,
    settings: DatabricksAppSettings,
    client_provider: ClientProvider,
    authenticator: Authenticator,
    resource_provider: ResourceProvider,
    api_factory: ApiFactory,
    api_settings: ApiSettings,
    ui_factory: UiFactory,
    ui_settings: UiSettings,
    lifecycle: CarrierLifecycle,
) -> ASGIApp:
    """Build a single ASGI host. No identity, database, or client is discovered."""
    if (
        isinstance(authenticator, DatabricksIdentityResolver)
        and authenticator.trusted_ingress != settings.trusted_ingress
    ):
        raise ValueError("identity_ingress_mismatch")
    if (
        isinstance(resource_provider, DatabricksResources)
        and resource_provider.bindings != settings.resource_bindings
    ):
        raise ValueError("resource_bindings_mismatch")
    prefix = settings.path_prefix
    api_prefix = prefix + api_settings.api_prefix
    configured_api = api_settings.model_copy(update={"api_prefix": api_prefix})
    configured_ui = replace(
        ui_settings,
        mount_path=prefix,
        api_discovery_path=api_prefix + "/capabilities",
    )
    api_app = api_factory(
        clients=client_provider,
        authenticator=authenticator,
        settings=configured_api,
    )
    ui_app = ui_factory(settings=configured_ui)
    return CarrierASGI(
        api=cast(ASGIApp, api_app),
        ui=ui_app,
        api_prefix=api_prefix,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        lifecycle=lifecycle,
        resources=resource_provider,
    )


__all__ = ["CarrierASGI", "create_carrier_app"]
