"""Volatile conformance store using the same projection/lease implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Literal, Self

from tether.stores.jsonl.store import JsonlSessionStore, utc_now
from tether.types import JsonValue
from tether.types.execution import Clock
from tether.types.storage import FileRecord, RootBusy, StorageLimits


class MemoryFiles:
    durability: Literal["none"] = "none"

    def __init__(self, limits: StorageLimits | None = None) -> None:
        self.limits = limits or StorageLimits()
        self.records: dict[str, list[FileRecord]] = {"journal": [], "history": []}
        self.claimed: set[str] = set()

    def claim(self, name: Literal["journal", "history"]) -> None:
        if name in self.claimed:
            raise RootBusy("adapter_already_open")
        self.claimed.add(name)

    async def scan(self, name: Literal["journal", "history"]) -> AsyncIterator[FileRecord]:
        for record in self.records[name]:
            yield record.model_copy(deep=True)

    async def append_file(
        self,
        name: Literal["journal", "history"],
        value: JsonValue,
        *,
        guard: Callable[[], None] | None = None,
    ) -> int:
        if guard is not None:
            guard()
        offset = len(self.records[name])
        self.records[name].append(FileRecord(offset=offset, value=value).model_copy(deep=True))
        return offset

    async def read_file(self, name: Literal["journal", "history"], offset: int) -> JsonValue:
        return self.records[name][offset].model_copy(deep=True).value


class MemorySessionStore(JsonlSessionStore):
    @classmethod
    async def create_store(
        cls, *, limits: StorageLimits | None = None, clock: Clock = utc_now
    ) -> Self:
        return await cls.open(MemoryFiles(limits), clock=clock)
