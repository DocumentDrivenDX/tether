"""Optional archive boundaries; original execution and history remain independent."""

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, Self

from pydantic import model_validator

from tether.types import JsonValue, Payload, PositiveInt
from tether.types.execution import Actor, HistoryRef
from tether.types.history import HistoryValue
from tether.types.storage import (
    ArchiveBundle,
    EntryPage,
    Page,
    SessionQuery,
    SessionSummary,
    SessionView,
    StoreError,
)


class ArchiveReadError(StoreError):
    def __init__(self, status: Literal["pending", "denied", "unavailable"]) -> None:
        self.status = status
        super().__init__("archive_" + status)


class NotRestorable(StoreError):
    pass


class ArchiveLimits(Payload):
    max_sessions: PositiveInt
    max_entries: PositiveInt
    max_bytes: PositiveInt


class ArchiveCapabilities(Payload):
    kind: Literal["authoritative", "derived"]
    writable: bool
    lossless: bool
    restorable: bool
    call_detail: bool = False
    content: Literal["full", "facts_only"]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.kind == "derived" and (self.lossless or self.restorable):
            raise ValueError("derived_archive_capabilities")
        if (self.lossless or self.restorable) and self.content != "full":
            raise ValueError("archive_reference_closure_required")
        if self.restorable and not self.lossless:
            raise ValueError("archive_reference_closure_required")
        return self


class ArchivedSession(Payload):
    view: SessionView
    bundle: ArchiveBundle


class LegacyCallDetail(Payload):
    request_id: str
    status: Literal["ready", "pending", "denied", "unavailable"]
    request: JsonValue = None
    response: JsonValue = None


class ArchiveSink(Protocol):
    capabilities: ArchiveCapabilities

    async def archive(self, session: ArchivedSession) -> None: ...


class ArchiveSource(Protocol):
    capabilities: ArchiveCapabilities

    async def list(
        self, query: SessionQuery, *, cursor: str | None, limit: int, actor: Actor
    ) -> Page[SessionSummary]: ...
    async def get(self, session_id: str, *, actor: Actor) -> SessionView | None: ...
    async def entries(
        self, session_id: str, *, actor: Actor, start: int, limit: int, reverse: bool
    ) -> EntryPage: ...
    async def read(
        self, refs: Sequence[HistoryRef], *, actor: Actor
    ) -> tuple[HistoryValue, ...]: ...
    async def restore(self, session_id: str, *, actor: Actor) -> ArchivedSession: ...
    async def call_detail(
        self, request_ids: Sequence[str], *, actor: Actor
    ) -> Mapping[str, LegacyCallDetail]: ...
