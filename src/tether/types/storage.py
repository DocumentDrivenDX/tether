"""Session journal/profile values; storage exceptions never become provider errors."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from tether.types import Payload, PositiveInt, PositiveSeconds, TokenUsage
from tether.types.execution import Entry, SessionState, TurnLog


class StoreError(Exception):
    pass


class IntegrityError(StoreError):
    pass


class UnknownSession(StoreError):
    pass


class LeaseLost(StoreError):
    pass


class TurnInProgress(StoreError):
    pass


class SessionClosed(StoreError):
    pass


class RootBusy(StoreError):
    pass


class StorageLimits(Payload):
    max_batch_bytes: PositiveInt = 4 * 1024 * 1024
    max_pending_io: PositiveInt = 32
    shutdown_seconds: PositiveSeconds = 5
    io_seconds: PositiveSeconds = 30
    max_startup_batches: PositiveInt = 1000000
    max_index_bytes: PositiveInt = 128 * 1024 * 1024
    lease_seconds: PositiveSeconds = 30
    page_limit: PositiveInt = 1000
    feed_retention: PositiveInt = 10000


class StoreCapabilities(Payload):
    durability: Literal["none", "process", "os"]
    lease: Literal["process", "cross_process"]
    fencing: bool
    notifications: bool = False


class SessionInit(Payload):
    ref: str | None = None
    spec: str | None = None
    title: str | None = None
    tags: tuple[str, ...] = ()
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class SessionSummary(Payload):
    id: str
    owner: str
    ref: str | None = None
    title: str | None = None
    tags: tuple[str, ...] = ()
    status: Literal["idle", "running", "waiting", "interrupted", "closed"] = "idle"
    resumable: bool = False
    tier: Literal["live", "archive"] = "live"
    partial: bool = False
    created_at: datetime
    updated_at: datetime
    last_turn_at: datetime | None = None
    entry_count: int = 0
    message_count: int = 0
    turn_count: int = 0
    preview: str | None = None
    usage: TokenUsage = Field(default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0))
    usage_complete: bool = True
    active_turn_id: str | None = None
    unresolved_effect_count: int = 0
    spec_id: str | None = None
    pending: bool = False
    last_seq: int = 0


class SessionView(Payload):
    summary: SessionSummary
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    specs: dict[str, JsonValue] = Field(default_factory=dict)
    pending: dict[str, JsonValue] | None = None
    tools: tuple[str, ...] = ()


class SessionSnapshot(Payload):
    view: SessionView
    through_index: int
    seq: int


class EntryPage(Payload):
    items: tuple[Entry, ...]
    next_start: int | None
    partial: bool = False


class PersistedChange(Payload):
    seq: int
    session_id: str
    kind: Literal["updated", "removed"]
    summary: SessionSummary | None
    entries: tuple[Entry, ...]


class RecoveredTurn(Payload):
    session_id: str
    turn_id: str
    resumable: bool


class FileRecord(Payload):
    offset: int
    value: JsonValue


class JournalFiles(Protocol):
    @property
    def durability(self) -> Literal["none", "process", "os"]: ...

    limits: StorageLimits

    def claim(self, name: Literal["journal", "history"]) -> None: ...

    def scan(self, name: Literal["journal", "history"]) -> AsyncIterator[FileRecord]: ...
    async def append_file(
        self,
        name: Literal["journal", "history"],
        value: JsonValue,
        *,
        guard: Callable[[], None] | None = None,
    ) -> int: ...
    async def read_file(self, name: Literal["journal", "history"], offset: int) -> JsonValue: ...


class SessionStore(Protocol):
    capabilities: StoreCapabilities

    async def create(self, owner: str, init: SessionInit) -> SessionSummary: ...
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot: ...
    async def entries(
        self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool = False
    ) -> EntryPage: ...
    def turn(
        self, session_id: str, owner: str, *, turn_id: str
    ) -> AbstractAsyncContextManager[TurnLog]: ...
    async def restore(self, session: SessionArchive, *, created_at: datetime) -> SessionSummary: ...
    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]: ...


class SessionArchive(Payload):
    state: SessionState
    init: SessionInit
    entries: tuple[Entry, ...]
    # Content is kept separately; full restore must verify closure before importing facts.


class DiskBatch(Payload):
    version: Literal[1] = 1
    previous: str | None
    payload: JsonValue
    sha256: str


class FileHead(Payload):
    end: int
    sha256: str | None


class JournalEvent(Payload):
    version: Literal[1] = 1
    operation: Literal["create", "append", "lease", "heartbeat", "release", "restore"]
    session_id: str
    owner: str
    at: datetime
    init: SessionInit | None = None
    entries: tuple[Entry, ...] = ()
    summary: SessionSummary | None = None
    change: PersistedChange | None = None
    generation: int = 0
    expires_at: datetime | None = None
    turn_id: str | None = None


class ArchiveBundle(Payload):
    version: Literal[1] = 1
    session: SessionArchive
    created_at: datetime
    records: tuple[dict[str, JsonValue], ...]
    sha256: str
