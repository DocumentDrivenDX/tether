"""HTTP v1 request and response payloads. Core values stay transport neutral."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from tether.client import (
    Answer,
    AttachmentRef,
    EntriesAppended,
    HistoryRef,
    Lagged,
    Page,
    ReplyReset,
    ReplyText,
    ResyncRequired,
    SessionRemoved,
    SessionSummary,
    SessionUpdated,
    SessionView,
)

CounterString: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9][0-9]*)$"),
    Field(json_schema_extra={"format": "tether-counter", "x-tether-counter": True}),
]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClientCapabilities(WireModel):
    methods: dict[str, Literal["supported", "unsupported"]]
    tiers: dict[Literal["live", "archive"], Literal["available", "unavailable"]]


class AcceptedTurn(WireModel):
    request_id: str | None = Field(default=None, min_length=1)
    turn_id: str = Field(min_length=1)
    state: Literal["accepted"] = "accepted"
    result_url: str


class ConfirmResponse(WireModel):
    accepted: Literal[False] = False
    state: SessionView


class CreateSessionRequest(WireModel):
    request_id: str = Field(min_length=1)
    spec: str | None = None
    title: str | None = None
    tags: tuple[str, ...] = ()
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class UpdateSessionRequest(WireModel):
    title: str | None = None
    tags: tuple[str, ...] | None = None
    settings: dict[str, JsonValue] | None = None


class SendRequest(WireModel):
    request_id: str = Field(min_length=1)
    text: str
    attachments: tuple[AttachmentRef, ...] = ()
    spec: str | None = None


class ConfirmRequest(WireModel):
    answer: Answer


class ResolveRequest(WireModel):
    outcome: Literal["completed", "not_completed"]
    reason: str


class HistoryLinkRequest(WireModel):
    message_index: CounterString
    previous: HistoryRef
    verified: HistoryRef
    key: str = Field(min_length=1)


class CallDetailRequest(WireModel):
    model_call_ids: tuple[str, ...]


class SessionUpdatedEvent(SessionUpdated):
    type: Literal["session_updated"] = Field()


class SessionRemovedEvent(SessionRemoved):
    type: Literal["session_removed"] = Field()


class EntriesAppendedEvent(EntriesAppended):
    type: Literal["entries_appended"] = Field()


class ReplyTextEvent(ReplyText):
    type: Literal["reply_text"] = Field()
    offset: int


class ReplyResetEvent(ReplyReset):
    type: Literal["reply_reset"] = Field()


class LaggedEvent(Lagged):
    type: Literal["lagged"] = Field()


class ResyncRequiredEvent(ResyncRequired):
    type: Literal["resync_required"] = Field()


ChangeEvent: TypeAlias = Annotated[
    SessionUpdatedEvent
    | SessionRemovedEvent
    | EntriesAppendedEvent
    | ReplyTextEvent
    | ReplyResetEvent
    | LaggedEvent
    | ResyncRequiredEvent,
    Field(discriminator="type"),
]


class ProblemDetails(WireModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None
    code: str
    retryable: bool
    retry_after_seconds: float | None = None
    locator: dict[str, str] | None = None


# Named aliases let code generation retain useful exported names.
PageSessionSummary: TypeAlias = Page[SessionSummary]


__all__ = [
    "AcceptedTurn",
    "CallDetailRequest",
    "ChangeEvent",
    "ClientCapabilities",
    "ConfirmRequest",
    "ConfirmResponse",
    "CreateSessionRequest",
    "HistoryLinkRequest",
    "ProblemDetails",
    "ResolveRequest",
    "SendRequest",
    "UpdateSessionRequest",
]
