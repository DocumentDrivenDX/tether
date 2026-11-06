"""Transport-neutral public client protocol and values."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Literal, Protocol

from tether.types import AttachmentRef, Credential, JsonValue
from tether.types.client import (
    UNSET,
    CallDetail,
    Change,
    ClientCapabilities,
    HistoryPage,
    TurnHandle,
    Unset,
)
from tether.types.execution import (
    Actor,
    Answer,
    Entry,
    HistoryRef,
    PendingConfirmation,
    TurnResult,
)
from tether.types.storage import (
    EffectPage,
    EntryPage,
    Page,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
)


class ClientReadProtocol(Protocol):
    """Transport-neutral reads, including the resumable change feed."""

    async def list(
        self,
        query: SessionQuery | None = None,
        *,
        tier: Literal["live", "archive"] = "live",
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[SessionSummary]: ...

    async def get(
        self, session_id: str, *, tier: Literal["live", "archive"] | None = None
    ) -> SessionView: ...

    async def entries(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] | None = None,
        start: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> EntryPage: ...

    async def history(
        self,
        session_id: str,
        *,
        tier: Literal["live", "archive"] = "live",
        start: int = 0,
        limit: int = 100,
    ) -> HistoryPage: ...

    async def pending(self, session_id: str) -> PendingConfirmation | None: ...
    async def snapshot(self, session_id: str) -> SessionSnapshot: ...
    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage: ...
    async def call_detail(
        self, session_id: str, model_call_ids: Sequence[str]
    ) -> tuple[CallDetail, ...]: ...
    def follow(
        self, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncIterator[Change]: ...


class ClientWriteProtocol(Protocol):
    """Transport-neutral session commands and accepted-turn outcomes."""

    @property
    def capabilities(self) -> ClientCapabilities: ...

    async def find(self, ref: str) -> SessionSummary | None: ...
    async def create(
        self,
        *,
        ref: str | None = None,
        spec: str | None = None,
        title: str | None = None,
        tags: Sequence[str] = (),
        settings: Mapping[str, JsonValue] | None = None,
    ) -> SessionSummary: ...
    async def send(
        self,
        session_id: str,
        text: str,
        *,
        request_id: str | None = None,
        attachments: Sequence[AttachmentRef] = (),
        spec: str | None = None,
    ) -> TurnHandle: ...
    async def confirm(
        self, session_id: str, invocation_id: str, answer: Answer
    ) -> TurnHandle | None: ...
    async def resume(self, session_id: str) -> TurnHandle: ...
    async def resolve(
        self,
        session_id: str,
        invocation_id: str,
        outcome: Literal["completed", "not_completed"],
        *,
        reason: str,
    ) -> SessionSummary: ...
    async def cancel(self, session_id: str) -> bool: ...
    async def cancel_turn(self, session_id: str, turn_id: str) -> bool: ...
    async def update(
        self,
        session_id: str,
        *,
        title: str | Unset | None = UNSET,
        tags: Sequence[str] | None = None,
        settings: Mapping[str, JsonValue] | None = None,
    ) -> SessionSummary: ...
    async def close(self, session_id: str) -> SessionSummary: ...
    async def restore(self, session_id: str) -> SessionSummary: ...
    async def link_history(
        self,
        session_id: str,
        message_index: int,
        previous: HistoryRef,
        verified: HistoryRef,
        *,
        key: str,
    ) -> Entry: ...
    async def turn_result(self, session_id: str, turn_id: str) -> TurnResult | None: ...


class ClientProtocol(ClientReadProtocol, ClientWriteProtocol, Protocol):
    """Combined client protocol for embedded and remote consumers."""


class ClientProvider(Protocol):
    def __call__(self, actor: Actor) -> ClientProtocol: ...


class CredentialProvider(Protocol):
    async def __call__(self) -> Credential: ...
