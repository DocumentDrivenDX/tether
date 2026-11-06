"""Declared platform resources with an explicit, caller-owned acquisition seam."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Protocol

from pydantic import Field, field_validator

from tether.client import Payload


class ResourceRef(Payload):
    """A non-secret resource name and the scopes requested for it."""

    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    scopes: tuple[str, ...] = ()

    @field_validator("name", "kind")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not value.isascii() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid_resource_reference")
        return value


class ResourceLease(Protocol):
    """A resource value whose ownership and cleanup are explicit."""

    @property
    def owned(self) -> bool: ...

    @property
    def value(self) -> object: ...

    async def close(self) -> None: ...


class ResourceProvider(Protocol):
    async def acquire(self, ref: ResourceRef) -> ResourceLease: ...


class ResourceAcquirer(Protocol):
    def __call__(self, ref: ResourceRef, /) -> Awaitable[ResourceLease]: ...


class DatabricksResources:
    """Acquire only declared bindings; close each owned lease once."""

    def __init__(self, bindings: tuple[ResourceRef, ...], acquire: ResourceAcquirer) -> None:
        names = tuple(ref.name for ref in bindings)
        if len(set(names)) != len(names):
            raise ValueError("duplicate_resource_binding")
        self._bindings = {ref.name: ref for ref in bindings}
        self._acquire = acquire
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._leases: dict[str, ResourceLease] = {}
        self._closed = False

    @classmethod
    def from_bindings(
        cls, bindings: tuple[ResourceRef, ...], *, acquire: ResourceAcquirer
    ) -> DatabricksResources:
        return cls(bindings, acquire)

    @property
    def bindings(self) -> tuple[ResourceRef, ...]:
        return tuple(self._bindings.values())

    async def acquire(self, ref: ResourceRef) -> ResourceLease:
        if self._closed:
            raise RuntimeError("carrier_resources_closed")
        declared = self._bindings.get(ref.name)
        if declared is None or declared != ref:
            raise PermissionError("resource_not_declared")
        lease = await self._acquire(declared)
        async with self._lock:
            if self._closed:
                if lease.owned:
                    await lease.close()
                raise RuntimeError("carrier_resources_closed")
            if lease.owned:
                self._leases[ref.name] = lease
        return lease

    async def aclose(self) -> tuple[str, ...]:
        """Close owned leases and report failures without touching borrowed values."""
        async with self._close_lock:
            async with self._lock:
                self._closed = True
                leases = tuple(self._leases.items())
            outstanding: list[str] = []
            for name, lease in reversed(leases):
                try:
                    await lease.close()
                except Exception:
                    outstanding.append(name)
                else:
                    async with self._lock:
                        self._leases.pop(name, None)
            return tuple(outstanding)


__all__ = [
    "DatabricksResources",
    "ResourceAcquirer",
    "ResourceLease",
    "ResourceProvider",
    "ResourceRef",
]
