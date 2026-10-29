"""Client read bounds and authoritative versus provisional change values."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol

from tether.types import AttemptIdentity, Payload, PositiveInt, PositiveSeconds, ResponseReceipt
from tether.types.execution import Entry, TurnResult
from tether.types.history import HistoryStatus, HistoryValue
from tether.types.storage import SessionSummary, StoreError


class Unset(Enum):
    omitted = "omitted"


UNSET = Unset.omitted


class ReadLimit(StoreError):
    pass


class UnknownModelCall(StoreError):
    pass


class RequestConflict(StoreError):
    pass


CLIENT_DEFAULTS_VERSION = 1


class ClientLimits(Payload):
    max_mutation_receipt_bytes: PositiveInt = 4 * 1024 * 1024
    max_page_size: PositiveInt = 100
    max_call_ids: PositiveInt = 8
    max_read_bytes: PositiveInt = 1024 * 1024
    max_lookup_entries: PositiveInt = 1000
    max_history_link_hops: PositiveInt = 8
    read_timeout_seconds: PositiveSeconds = 5
    heartbeat_interval_seconds: PositiveSeconds = 1
    shutdown_timeout_seconds: PositiveSeconds = 5
    follower_queue_size: PositiveInt = 32
    max_followers: PositiveInt = 8
    max_active_turns: PositiveInt = 8
    max_request_bytes: PositiveInt = 65536


class HistorySlot(Payload):
    entry_index: int
    value: HistoryValue


class HistoryPage(Payload):
    items: tuple[HistorySlot, ...]
    next_start: int | None
    partial: bool = False


class CallDetail(Payload):
    identity: AttemptIdentity
    receipt: ResponseReceipt | None = None
    receipts: tuple[ResponseReceipt, ...] = ()
    status: HistoryStatus
    observations: tuple[HistoryValue, ...] = ()
    reason: str | None = None


class SessionUpdated(Payload):
    seq: int
    summary: SessionSummary
    last_in_commit: bool


class SessionRemoved(Payload):
    seq: int
    session_id: str
    last_in_commit: bool


class EntriesAppended(Payload):
    seq: int
    session_id: str
    entries: tuple[Entry, ...]
    last_in_commit: bool


class ReplyText(Payload):
    session_id: str
    turn_id: str
    attempt_id: str
    offset: int
    text: str


class ReplyReset(Payload):
    session_id: str
    turn_id: str
    attempt_id: str
    reason: Literal["outage", "cancelled", "reconnect"]


class Lagged(Payload):
    missed: int


class ResyncRequired(Payload):
    reason: Literal["too_old", "gap"]


Change = (
    SessionUpdated
    | SessionRemoved
    | EntriesAppended
    | ReplyText
    | ReplyReset
    | Lagged
    | ResyncRequired
)


class TurnHandle(Protocol):
    @property
    def turn_id(self) -> str: ...

    async def result(self) -> TurnResult: ...
    async def cancel(self) -> None: ...
