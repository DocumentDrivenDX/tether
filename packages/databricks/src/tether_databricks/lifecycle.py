"""Bounded, truthful shutdown contracts and a composable close owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ShutdownReport:
    complete: bool
    outstanding: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()


class CarrierLifecycle(Protocol):
    async def close(self, *, timeout_seconds: float) -> ShutdownReport: ...


class ResourceLifecycle:
    """Close an ordered chain of owned resources within the caller's deadline.

    A timed-out close task remains retained and is never canceled by this helper.
    False or an exception retains that resource and every later dependency.
    A later explicit close retries remaining resources, preserving earlier closes.
    """

    def __init__(self, closers: Mapping[str, Callable[[], Awaitable[bool | None]]]) -> None:
        if any(not name or not name.isascii() for name in closers):
            raise ValueError("invalid_lifecycle_resource_name")
        self._closers = dict(closers)
        self._task: asyncio.Task[None] | None = None
        self._closed: list[str] = []
        self._lock = asyncio.Lock()

    async def close(self, *, timeout_seconds: float) -> ShutdownReport:
        if not 0 < timeout_seconds < float("inf"):
            raise ValueError("invalid_shutdown_timeout")
        async with self._lock:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._close_all(), name="tether-close-resources")
            task = self._task
        await asyncio.wait([task], timeout=timeout_seconds)
        outstanding = tuple(name for name in self._closers if name not in self._closed)
        return ShutdownReport(
            complete=not outstanding,
            outstanding=outstanding,
            closed=tuple(self._closed),
        )

    async def _close_all(self) -> None:
        for name, closer in self._closers.items():
            if name in self._closed:
                continue
            try:
                result = await closer()
                if result is False:
                    return
                if result is not None and result is not True:
                    raise TypeError("lifecycle_closer_must_return_bool_or_none")
            except Exception:
                return
            else:
                self._closed.append(name)


__all__ = ["CarrierLifecycle", "ResourceLifecycle", "ShutdownReport"]
