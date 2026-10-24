"""Session journal/profile values; storage exceptions never become provider errors."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import Field, JsonValue

from tether.types import Payload, PositiveInt, PositiveSeconds, TokenUsage
from tether.types.execution import Entry, PendingConfirmation, SessionState, TurnLog

SESSION_CONTRACT_VERSION = 2


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


class ChangeGap(StoreError):
    pass


class Unsupported(StoreError):
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
    change_poll_seconds: PositiveSeconds = 0.1


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


class SpecRef(Payload):
    id: str
    version: str | None = None
    digest: str | None = None


class ToolInfo(Payload):
    name: str
    version: str | None = None
    tool_digest: str | None = None
    description: str | None = None


class SessionView(Payload):
    summary: SessionSummary
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    specs: dict[str, SpecRef] = Field(default_factory=dict)
    pending: PendingConfirmation | None = None
    tools: tuple[ToolInfo, ...] = ()
    tools_status: Literal["not_loaded", "ready", "unsupported"] = "not_loaded"


class SessionSnapshot(Payload):
    view: SessionView
    through_index: int
    seq: int


class EntryPage(Payload):
    items: tuple[Entry, ...]
    next_start: int | None
    partial: bool = False


T = TypeVar("T")


class Page(Payload, Generic[T]):
    items: tuple[T, ...]
    next: str | None = None


class SessionQuery(Payload):
    owner: str
    ref: str | None = None
    status: frozenset[str] | None = None
    tags_all: tuple[str, ...] = ()
    updated_after: datetime | None = None
    updated_before: datetime | None = None
    text: str | None = None
    order: Literal["updated_desc", "created_desc", "created_asc"] = "updated_desc"


class EffectInfo(Payload):
    invocation_id: str
    tool: str
    intent_index: int
    reason: str


class EffectPage(Payload):
    items: tuple[EffectInfo, ...]
    next_start: int | None = None


class SessionPatch(Payload):
    title: str | None = None
    tags: tuple[str, ...] | None = None
    settings: dict[str, JsonValue] | None = None


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

    async def aclose(self) -> bool: ...

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

    async def aclose(self) -> bool: ...

    async def create(self, owner: str, init: SessionInit) -> SessionSummary: ...
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def list(
        self, query: SessionQuery, *, cursor: str | None = None, limit: int = 50
    ) -> Page[SessionSummary]: ...
    async def find(self, owner: str, ref: str) -> SessionSummary | None: ...
    async def effects(
        self, session_id: str, owner: str, *, start: int = 0, limit: int = 50
    ) -> EffectPage: ...
    def changes(
        self, owner: str, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncIterator[PersistedChange]: ...
    async def update(self, session_id: str, owner: str, patch: SessionPatch) -> SessionSummary: ...
    async def lookup(
        self,
        session_id: str,
        owner: str,
        *,
        kind: Literal["key", "invocation", "model_call", "turn"],
        value: str,
        limit: int = 1000,
        max_bytes: int | None = None,
    ) -> tuple[Entry, ...]: ...
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
