"""Bounded static hosting; API, identity and runtime ownership remain external."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol, cast

Scope = Mapping[str, object]
Message = dict[str, object]
Receive = Callable[[], Awaitable[Mapping[str, object]]]
Send = Callable[[Message], Awaitable[None]]


class ASGIApp(Protocol):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: ...


class AssetConfigurationError(ValueError):
    """The selected asset cohort cannot be served safely."""


def _prefix(value: str) -> str:
    if value in {"", "/"}:
        return ""
    if not value.startswith("/") or any(x in value for x in ("?", "#", "\\")):
        raise ValueError("invalid_mount_path")
    value = value.rstrip("/")
    if any(x in {".", "..", ""} for x in value[1:].split("/")):
        raise ValueError("invalid_mount_path")
    return value.rstrip("/")


@dataclass(frozen=True)
class UiSettings:
    mount_path: str = ""
    asset_root: str | None = None
    api_discovery_path: str = "/api/v1/capabilities"
    max_asset_bytes: int = 16 * 1024 * 1024
    max_assets: int = 1024
    max_total_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_path", _prefix(self.mount_path))
        path = self.api_discovery_path
        if not path.startswith("/") or any(x in path for x in ("?", "#", "\\")):
            raise ValueError("invalid_api_discovery_path")
        if self.max_asset_bytes <= 0 or self.max_assets <= 0 or self.max_total_bytes <= 0:
            raise ValueError("invalid_asset_limits")


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise AssetConfigurationError("asset_too_large")
    return value


def _load(settings: UiSettings) -> dict[str, bytes]:
    asset_root = settings.asset_root or str(files("tether_web_ui").joinpath("assets"))
    root = Path(asset_root).resolve()
    try:
        document: object = json.loads(_read(root / "tether-assets.json", settings.max_asset_bytes))
    except (OSError, ValueError) as error:
        raise AssetConfigurationError("asset_manifest_unavailable") from error
    if not isinstance(document, dict):
        raise AssetConfigurationError("asset_manifest_invalid")
    inventory = cast(dict[object, object], document)
    if len(inventory) > settings.max_assets:
        raise AssetConfigurationError("asset_manifest_invalid")
    result: dict[str, bytes] = {}
    total = 0
    for name, digest in inventory.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise AssetConfigurationError("asset_manifest_invalid")
        asset = root.joinpath(name).resolve()
        if not asset.is_relative_to(root) or not asset.is_file():
            raise AssetConfigurationError("asset_unavailable")
        value = _read(asset, min(settings.max_asset_bytes, settings.max_total_bytes - total))
        total += len(value)
        if hashlib.sha256(value).hexdigest() != digest:
            raise AssetConfigurationError("asset_integrity_error")
        result[name] = value
    if "index.html" not in result:
        raise AssetConfigurationError("asset_entry_unavailable")
    return result


class StaticApp:
    """Serve only the verified asset inventory without owning another application."""

    def __init__(self, settings: UiSettings) -> None:
        self.settings = settings
        self._assets: dict[str, bytes] = {}
        self._loading: asyncio.Task[dict[str, bytes]] | None = None

    async def _ready(self) -> None:
        if self._loading is None:
            self._loading = asyncio.create_task(asyncio.to_thread(_load, self.settings))
        self._assets = await asyncio.shield(self._loading)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
        elif scope.get("type") == "http":
            try:
                await self._ready()
            except (OSError, ValueError):
                await self._response(
                    send,
                    503,
                    b"Assets unavailable",
                    "text/plain",
                    head=scope.get("method") == "HEAD",
                )
                return
            await self._http(scope, send)
        else:
            raise ValueError("unsupported_asgi_scope")

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            event = await receive()
            if event.get("type") == "lifespan.startup":
                try:
                    await self._ready()
                except (OSError, ValueError):
                    await send({"type": "lifespan.startup.failed", "message": "Assets unavailable"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event.get("type") == "lifespan.shutdown":
                if self._loading is not None:
                    await asyncio.shield(self._loading)
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _location(self, scope: Scope) -> tuple[str, str] | None:
        path, root = scope.get("path", "/"), scope.get("root_path", "")
        if not isinstance(path, str) or not isinstance(root, str):
            return None
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root) :] or "/"
        mount = self.settings.mount_path
        if mount and not root.endswith(mount):
            if path != mount and not path.startswith(mount + "/"):
                return None
            path = path[len(mount) :] or "/"
            root += mount
        return path, root

    async def _http(self, scope: Scope, send: Send) -> None:
        method = scope.get("method")
        if method not in {"GET", "HEAD"}:
            await self._response(send, 405, b"Method not allowed", "text/plain", head=False)
            return
        location = self._location(scope)
        if location is None:
            await self._not_found(send, method == "HEAD")
            return
        path, base = location
        if any(part in {".", ".."} for part in path.split("/")) or "\\" in path:
            await self._not_found(send, method == "HEAD")
            return
        if path == "/.tether/config.json":
            body = json.dumps(
                {"base_path": base, "api_discovery_path": self.settings.api_discovery_path}
            ).encode()
            await self._response(send, 200, body, "application/json", head=method == "HEAD")
            return
        if path == "/api" or path.startswith("/api/"):
            await self._not_found(send, method == "HEAD")
            return
        name = path.lstrip("/")
        if name not in self._assets:
            if "." in name.rsplit("/", 1)[-1]:
                await self._not_found(send, method == "HEAD")
                return
            name = "index.html"
        body = self._assets[name]
        if name == "index.html":
            body = body.replace(b"__TETHER_BASE__", html.escape(base + "/", quote=True).encode())
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        hashed = bool(re.search(r"-[a-zA-Z0-9_-]{8,}\.", name))
        cache = "public, max-age=31536000, immutable" if hashed else "no-store"
        await self._response(send, 200, body, mime, head=method == "HEAD", cache=cache)

    async def _not_found(self, send: Send, head: bool) -> None:
        await self._response(send, 404, b'{"code":"not_found"}', "application/json", head=head)

    @staticmethod
    async def _response(
        send: Send, status: int, body: bytes, mime: str, *, head: bool, cache: str = "no-store"
    ) -> None:
        headers = [
            (b"content-type", mime.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", cache.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"same-origin"),
            (
                b"content-security-policy",
                b"default-src 'self'; script-src 'self'; style-src 'self'; "
                b"img-src 'self' data:; connect-src 'self'; base-uri 'self'; "
                b"frame-ancestors 'self'; object-src 'none'",
            ),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if head else body})


class UiFactory(Protocol):
    def __call__(self, *, settings: UiSettings) -> ASGIApp: ...


def create_static_app(*, settings: UiSettings) -> ASGIApp:
    """Validate a packaged asset cohort and return an independently mountable app."""
    return StaticApp(settings)
