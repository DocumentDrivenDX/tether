"""Authoritative content values and ports (CONTRACT-005)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, Self

from pydantic import Field, JsonValue, model_validator

from tether.types import AttemptIdentity, Message, Payload, ResponseReceipt
from tether.types.execution import Actor, HistoryRef

HISTORY_CONTRACT_VERSION = 2

HistoryStatus = Literal[
    "ready",
    "pending",
    "denied",
    "unavailable",
    "missing",
    "partial",
    "ambiguous",
    "unsupported",
    "integrity_error",
]


class HistoryRecord(Payload):
    schema_version: Literal[1] = 1
    kind: Literal["message", "model_exchange", "summary"]
    message: Message | None = None
    summary: str | None = None
    request: JsonValue | None = None
    response: JsonValue | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.kind == "message":
            valid = (
                self.message is not None and self.summary is self.request is self.response is None
            )
        elif self.kind == "summary":
            valid = (
                self.summary is not None and self.message is self.request is self.response is None
            )
        else:
            valid = self.message is self.summary is None
        if not valid:
            raise ValueError("invalid_history_record")
        return self


class HistoryValue(Payload):
    ref: HistoryRef
    status: HistoryStatus
    record: HistoryRecord | None = None
    reason: str | None = None
    retry_after_seconds: float | None = None
    source_locator: dict[str, str] = Field(default_factory=dict)


class HistoryCapabilities(Payload):
    writable: bool
    externally_recorded: bool
    durability: Literal["none", "process", "os", "external"]
    delivery: Literal["immediate", "eventual"]
    coverage: frozenset[str]


class ExchangeBinding(Payload):
    identity: AttemptIdentity
    receipt: ResponseReceipt
    owner: str
    request_digest: str
    response_digest: str | None
    exchange_sha256: str
    source_sha256: str | None = None
    codec: str
    response_kind: Literal["assistant", "summary", "none"]


class ExchangeRefs(Payload):
    exchange: HistoryRef
    source: HistoryRef | None = None


class HistoryProvider(Protocol):
    id: str
    capabilities: HistoryCapabilities

    async def put(
        self, *, actor: Actor, session_id: str, key: str, record: HistoryRecord
    ) -> HistoryRef: ...
    async def read(
        self, refs: Sequence[HistoryRef], *, actor: Actor
    ) -> tuple[HistoryValue, ...]: ...
    async def bind_exchange(self, *, actor: Actor, binding: ExchangeBinding) -> ExchangeRefs: ...


class StoredHistory(Payload):
    ref: HistoryRef
    record: HistoryRecord
