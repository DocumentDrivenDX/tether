"""Provider-neutral slice 2 execution values; journal/content remain separate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool
from pydantic.json_schema import SkipJsonSchema

from tether.types import (
    AttachmentRef,
    Credential,
    Message,
    Payload,
    PositiveInt,
    PositiveSeconds,
    Purpose,
    TokenUsage,
    ToolCall,
)
from tether.types.lifetime import WorkerLifetime

ENGINE_CONTRACT_VERSION = 4

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

Effect = Literal["read_only", "effectful"]
Replay = Literal["safe", "unsafe"]


class Actor(Payload):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    id: str
    kind: str = "user"
    principal: str | None = Field(default=None, min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    credential: SkipJsonSchema[Credential | None] = Field(default=None, exclude=True, repr=False)


def copy_actor(actor: Actor) -> Actor:
    return Actor.model_validate(actor.model_dump(mode="python")).model_copy(
        update={"credential": actor.credential}
    )


class AgentSpec(Payload):
    schema_version: Literal[1] = 1
    id: str
    version: str
    title: str
    instructions: str
    model: str
    tools: tuple[str, ...] = ()
    max_steps: PositiveInt = 20
    ext: dict[str, JsonValue] = Field(default_factory=dict)


class UsageSoFar(Payload):
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0
    model_calls: int = 0
    summary_calls: int = 0
    usage_complete: bool = True
    by_purpose: dict[str, TokenUsage] = Field(default_factory=dict)
    missing_by_purpose: dict[str, int] = Field(default_factory=dict)


class AuthzRequest(Payload):
    actor: Actor
    action: str
    resource: str
    held: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    requires: frozenset[str] = frozenset()
    permitted: frozenset[str] | None = None
    purpose: Purpose | None = None
    usage: UsageSoFar | None = None


class Decision(Payload):
    model_config = ConfigDict(revalidate_instances="always")

    allowed: StrictBool
    reason: str


class HistoryRef(Payload):
    provider: str
    key: str
    sha256: str
    codec: str = "tether-message-v1"
    owner: str = ""
    session_id: str = ""
    kind: Literal["message", "model_exchange", "summary"] = "message"
    component: Literal["record", "request", "response", "assistant", "summary"] = "record"


class CallManifest(Payload):
    registry_generation: str | None = Field(default=None, exclude_if=lambda value: value is None)
    position: int
    call_id: str
    tool: str
    args_digest: str
    tool_digest: str
    effect: Effect
    replay: Replay


class NewEntry(Payload):
    kind: str
    turn_id: str | None = None
    key: str | None = None
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    history_ref: HistoryRef | None = None
    call_manifest: tuple[CallManifest, ...] = ()


class Entry(NewEntry):
    index: int
    at: datetime


class SessionState(Payload):
    id: str
    owner: str
    held: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    approval: Literal["auto", "manual"] = "auto"


class ExecutionLimits(Payload):
    tool_timeout_seconds: PositiveSeconds = 30
    hook_timeout_seconds: PositiveSeconds = 5
    max_argument_bytes: PositiveInt = 65536
    max_result_bytes: PositiveInt = 1048576
    max_workers: PositiveInt = 8
    max_worker_operations: PositiveInt = 32
    shutdown_timeout_seconds: PositiveSeconds = 1
    approval_ttl_seconds: PositiveSeconds | None = None
    max_resumes: PositiveInt = 3
    max_history_link_hops: PositiveInt = 8


class ApprovalBinding(Payload):
    proposal_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
    shown_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
    executable_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
    session_id: str
    invocation_id: str
    args_digest: str
    tool_digest: str
    spec_digest: str
    actor_id: str


class PendingConfirmation(ApprovalBinding):
    tool: str
    arguments: dict[str, JsonValue] | None
    history_ref: HistoryRef | None = None
    history_status: Literal["not_loaded"] | HistoryStatus = "ready"
    reason: str | None = None
    description: str = ""
    requested_at: datetime
    expires_at: datetime | None = None


class Answer(Payload):
    arguments: dict[str, JsonValue] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    approved: StrictBool
    note: str | None = None


class TurnStart(Payload):
    turn_id: str = Field(min_length=1)
    request_id: str | None = Field(default=None, min_length=1)
    requested_spec: str | None = None
    attachments: tuple[AttachmentRef, ...] = ()


class TurnResult(Payload):
    turn_id: str
    stop_reason: str
    reason: str | None = None
    messages: tuple[Message, ...] = ()
    pending: tuple[PendingConfirmation, ...] = ()


class SettlementStage(Payload):
    participant: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)


class ToolOutput(Payload):
    settlement: SettlementStage | None = Field(default=None, exclude_if=lambda value: value is None)
    text: str
    attachments: tuple[AttachmentRef, ...] = ()
    facts: dict[str, JsonValue] = Field(default_factory=dict)


V = TypeVar("V")


class ToolContext(Payload):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    actor: Actor
    invocation_id: str
    scope: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    capabilities: SkipJsonSchema[Mapping[type, object]] = Field(
        default_factory=lambda: dict[type, object](), exclude=True, repr=False
    )
    retrieve: SkipJsonSchema[Callable[[str, Callable[[], object]], Awaitable[object]]] = Field(
        exclude=True, repr=False
    )

    def capability(self, kind: type[V]) -> V:
        if kind not in self.capabilities:
            raise ExecutionDenied("capability_unavailable")
        return cast(V, self.capabilities[kind])

    async def read(self, resource: str, fetch: Callable[[], V | Awaitable[V]]) -> V:
        return cast(V, await self.retrieve(resource, fetch))


class Refusal(Payload):
    reason: str


class GuardVerdict(Payload):
    outcome: Literal["ok", "withheld", "denied"]
    reason: str | None = None
    facts: dict[str, JsonValue] = Field(default_factory=dict)


class Tool(Protocol):
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    requires: frozenset[str]
    model_visible: bool
    always_ask: bool
    effect: Effect
    replay: Replay
    timeout_seconds: float | None
    traits: dict[str, JsonValue]
    definition_hash: str | None

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool: ...
    def run(
        self, ctx: ToolContext, args: BaseModel
    ) -> str | ToolOutput | Awaitable[str | ToolOutput]: ...


class Policy(Protocol):
    async def authorize(self, request: AuthzRequest) -> Decision: ...


class TurnLog(Protocol):
    """Accepted entries and the optional lease-bound logical turn identity."""

    @property
    def turn_id(self) -> str | None: ...

    @property
    def workers(self) -> WorkerLifetime: ...

    @property
    def session(self) -> SessionState: ...

    @property
    def entries(self) -> Sequence[Entry]: ...

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]: ...


class MessageHistory(Protocol):
    """Message-only subset of HistoryProvider, extended with exchanges in slice 3."""

    async def put(
        self, *, actor: Actor, session_id: str, key: str, message: Message
    ) -> HistoryRef: ...
    async def read(self, ref: HistoryRef, *, actor: Actor, session_id: str) -> Message: ...


class Provider(Protocol):
    id: str
    contract: int

    def tools(self) -> Sequence[Tool]: ...
    def capabilities(
        self, actor: Actor, session: SessionState
    ) -> Mapping[type, object] | Awaitable[Mapping[type, object]]: ...
    def prompt_section(
        self, session: SessionState, spec: AgentSpec
    ) -> str | Awaitable[str | None] | None: ...
    def hidden_tools(
        self, session: SessionState, spec: AgentSpec
    ) -> frozenset[str] | Awaitable[frozenset[str]]: ...
    def before_request(
        self, session: SessionState, spec: AgentSpec, messages: tuple[Message, ...]
    ) -> tuple[Message, ...] | Awaitable[tuple[Message, ...]]: ...
    def before_call(
        self, session: SessionState, spec: AgentSpec, call: ToolCall
    ) -> Refusal | Awaitable[Refusal | None] | None: ...
    def after_call(
        self, session: SessionState, spec: AgentSpec, call: ToolCall, message: Message
    ) -> Message | Awaitable[Message]: ...


class GuardInput(Payload):
    tool: str
    output: ToolOutput
    traits: dict[str, JsonValue]
    facts: dict[str, JsonValue] = Field(default_factory=dict)


class ResultGuard(Protocol):
    id: str

    async def check(self, value: GuardInput, session: SessionState) -> GuardVerdict: ...


Clock = Callable[[], datetime]


class ExecutionDenied(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RetrievalDenied(ExecutionDenied):
    pass


class WorkerOutstanding(ExecutionDenied):
    pass


class EffectsUnresolved(ExecutionDenied):
    pass


class NotUnresolved(ExecutionDenied):
    pass


class ResolutionConflict(ExecutionDenied):
    pass


class Resolution(Payload):
    invocation_id: str = Field(min_length=1)
    outcome: Literal["completed", "not_completed"]
    reason: str = Field(min_length=1, max_length=1024)


class NotInterrupted(ExecutionDenied):
    pass


class ResumeLimit(ExecutionDenied):
    pass


class ResumeTool(Payload):
    tool_digest: str
    replay: Replay


class ResumeStep(Payload):
    kind: Literal["run", "rerun", "record_interrupted", "model_step"]
    call_entry_index: int | None = None
    call_position: int | None = None
    invocation_id: str | None = None


class ResumePlan(Payload):
    interrupted_turn_id: str
    steps: tuple[ResumeStep, ...]


class CommitUnknown(Exception):
    """Caller must not infer whether the append was accepted."""


class EntryConflict(Exception):
    pass
