"""Provider-neutral model payloads and value callback types (CONTRACT-001/005)."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.json_schema import SkipJsonSchema

MODEL_PORT_VERSION = 2
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]
PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Purpose = Literal["answer", "summary", "correction"]


class Payload(BaseModel):
    """Frozen fields; JSON containers are copied at trust boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class AttemptIdentity(Payload):
    session_id: str
    turn_id: str
    model_call_id: str
    attempt_id: str
    purpose: Purpose
    trace_id: str | None = None
    span_id: str | None = None


class ResponseReceipt(Payload):
    provider: str
    endpoint: str
    schema_profile: str
    provider_request_id: str | None = None
    provider_invocation_id: str | None = None
    response_id: str | None = None
    request_id_source: Literal["header", "body", "none"]


@runtime_checkable
class Credential(Protocol):
    async def authorization(self) -> str | None: ...


@runtime_checkable
class AttemptRecorder(Protocol):
    async def reserve(self) -> AttemptIdentity: ...
    async def start(
        self,
        identity: AttemptIdentity,
        *,
        request_digest: str,
        wire_request: JsonValue,
        codec: str = "model-port-v1",
    ) -> None: ...
    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
        usage: TokenUsage | None = None,
    ) -> None: ...


class ToolCall(Payload):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    arguments: dict[str, JsonValue]


class AttachmentRef(Payload):
    id: str
    filename: str
    media_type: str
    size_bytes: Count
    read: bool
    withheld: bool = False


class Message(Payload):
    role: Literal["user", "assistant", "tool"]
    content: str
    attachments: tuple[AttachmentRef, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    is_error: bool = False


class ToolSpec(Payload):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    description: str
    input_schema: dict[str, JsonValue]


class RequestTags(Payload):
    session_id: str
    principal: str
    spec_id: str
    spec_version: str
    turn_id: str
    model_call_id: str
    purpose: Purpose = "answer"
    context: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class ModelRequest(Payload):
    model_config = ConfigDict(
        frozen=True, extra="forbid", arbitrary_types_allowed=True, allow_inf_nan=False
    )
    model: str
    tags: RequestTags
    system: str
    messages: tuple[Message, ...]
    identity_mode: Literal["caller", "application", "anonymous"] = "caller"
    max_output_tokens: PositiveInt
    tools: tuple[ToolSpec, ...] = ()
    effort: Literal["high", "medium", "low"] | None = None
    traceparent: str | None = None
    timeout_seconds: PositiveSeconds | None = None
    credential: SkipJsonSchema[Credential | None] = Field(default=None, exclude=True, repr=False)
    attempts: SkipJsonSchema[AttemptRecorder | None] = Field(default=None, exclude=True, repr=False)
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class TokenUsage(Payload):
    input_tokens: Count
    output_tokens: Count
    cache_read_tokens: Count | None = None
    cache_write_tokens: Count | None = None
    reasoning_tokens: Count | None = None


class ModelResponse(Payload):
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    request_id: str | None = None
    usage: TokenUsage | None = None
    receipt: ResponseReceipt | None = None
    finish_reason: Literal["stop", "tool_calls", "length", "filtered", "unknown"]


class TextDelta(Payload):
    text: str


StreamItem = TextDelta | ModelResponse


class ModelExecutionLimits(Payload):
    max_response_bytes: PositiveInt
    max_tool_argument_bytes: PositiveInt
    max_attempts: PositiveInt
    request_timeout_seconds: PositiveSeconds
    max_retry_delay_seconds: PositiveSeconds


class ModelUnavailable(Exception):
    """A short metadata-only failure. Never retain an underlying exception."""

    def __init__(
        self, reason: str, request_id: str | None = None, receipt: ResponseReceipt | None = None
    ) -> None:
        valid = re.fullmatch(r"[a-z][a-z0-9_]*(?: [0-9]{3})?", reason)
        self.reason = reason if valid and len(reason) <= 64 else "model_unavailable"
        if reason == "unconfigured tier":
            self.reason = reason
        self.request_id = request_id
        self.receipt = receipt
        super().__init__(self.reason)


class ModelAttemptDenied(Exception):
    def __init__(self, reason: str = "model_denied") -> None:
        self.reason = reason if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) else "model_denied"
        super().__init__(self.reason)
