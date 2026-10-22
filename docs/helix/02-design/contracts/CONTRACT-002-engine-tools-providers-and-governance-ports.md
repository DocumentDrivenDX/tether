---
ddx:
  id: CONTRACT-002
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: architecture
      kind: informed_by
---

# CONTRACT-002: Engine, Tools, Providers, and Governance Ports

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-002
**Type**: library (Python ports)
**Version**: v1
**Related**: ADR-001, ADR-003, ADR-005, CONTRACT-001, CONTRACT-003

## Purpose

This contract fixes what an application implements to run the engine and what a provider implements to contribute a feature to it. It defines the turn the engine runs, the tool interface, the governance ports, the telemetry ports, and the order in which the engine runs its gates, because that order is what a provider may rely on and never change. The interface is asynchronous throughout and has no synchronous form (ADR-001).

## Scope and Boundaries

- In scope: identity, decisions, and the specification the loop reads; the engine's entry points; the policy, scope, specification-resolution, tool, provider, and result-guard ports; the audit sink and tracer; the gate sequence and its commit points; the in-process interaction port; cancellation and failure behavior; versioning.
- Out of scope: the model port (CONTRACT-001); the session log, store, and client API (CONTRACT-003); an application's manifest format, which the application compiles to an `AgentSpec`; the wire shapes of any server adapter.
- Owning system or team: tether core. A new port, a changed signature, or a reason the engine reads is a contract version change and an ADR-003 amendment, justified by a second application or a feature a provider cannot express.

## Normative Surface

Signatures are Python 3.11. Payload types are frozen `pydantic` models with `extra="forbid"`; ports are `typing.Protocol` classes. MUST, MUST NOT, SHOULD, and MAY are used as defined in RFC 2119.

**Asynchronous form.** A method shown `async def` is a coroutine function. A port that does no I/O is synchronous and is marked so: `Tracer`, `Clock`, `Ids`, `Tool.asks_first`, `Tool.drawn_from`, and `ScopeKind.source_within`. A tool or a provider method MAY be written as a plain `def`; the engine then runs it in a worker thread (section 4).

### 1. Identity, decisions, the spec, and the turn

```python
class Actor(BaseModel):  id: str; kind: str = "user"; attributes: dict[str, JsonValue] = {}   # who a call is for
class Action(StrEnum):
    CALL_MODEL = "call_model"; CALL_TOOL = "call_tool"; RETRIEVE = "retrieve"
    ENGAGE = "engage"; WIDEN_CONTEXT = "widen_context"; READ_SESSION = "read_session"
class Decision(BaseModel):  allowed: bool; reason: str                          # "allowed" when allowed
class ContextKey(BaseModel):  key: str; kind: str; mode: Literal["fixed", "grows"] = "fixed"
class AgentSpec(BaseModel):                                                      # what the loop reads of a task
    schema_version: int = 1
    id: str; version: str; title: str; instructions: str; model: str            # model is a tier
    tools: tuple[str, ...] = (); max_steps: int = 20; context: tuple[ContextKey, ...] = ()
    ext: dict[str, JsonValue] = {}                                               # blocks the engine passes untouched to providers, by namespace
class PendingConfirmation(BaseModel):  calls: tuple[ToolCall, ...]; hashes: dict[str, str]
StopReason = Literal["final", "step_limit", "model_denied", "model_unavailable",
                     "scope_required", "confirmation_required", "cancelled", "interrupted"]
class TurnResult(BaseModel):
    turn_id: str; stop_reason: StopReason; reason: str | None = None            # the outage's short cause
    messages: tuple[Message, ...] = (); suggestion: str | None = None

class Engine:                                                                    # constructed with the ports; no environment reads
    async def run_turn(self, actor: Actor, log: TurnLog, text: str, *, spec: AgentSpec | None = None,
                       attachments: tuple[AttachmentRef, ...] = (), events: EventSink | None = None,
                       parent: object | None = None, suggest: bool = True) -> TurnResult: ...
    async def answer(self, actor: Actor, log: TurnLog, call_id: str, answer: Answer, *,
                     events: EventSink | None = None, parent: object | None = None) -> TurnResult | None: ...
```

`TurnLog` is the session store's write handle for one turn (CONTRACT-003). `Engine.answer` returns `None` when `call_id` is not the pending call and changes nothing. The engine reads `Decision.reason` for the values `allowed`, `tool_not_permitted`, `scope_required`, `definition_changed`, and `entitlement_unavailable` (an infrastructure refusal, recorded and flagged in the session's `flags`); every other reason is recorded verbatim and treated as a denial. A provider's reason MUST be `x.<provider id>.<reason>`.

The engine owns the session's context (the held and bound scope values), tool registration, and tool resolution by name. Finding tools for the model, engagement and routing, stages, and skills are providers' and the application's, and read what they need from `AgentSpec.ext`. The session pins the `AgentSpec` it engaged; `schema_version` is part of the stored session and a store MUST preserve it.

### 2. Policy, scope binding, scope kinds, and spec resolution

```python
class AuthzRequest(BaseModel):
    actor: Actor; action: Action; resource: str
    held: dict[str, tuple[str, ...]]                 # the session's scope values, by key
    requires: frozenset[str] = frozenset()           # keys the call needs held
    permitted: frozenset[str] | None = None          # names the caller may use, when the action has a list

class Policy(Protocol):
    async def authorize(self, request: AuthzRequest) -> Decision: ...

class ScopeKind(Protocol):
    kind: str
    async def may_hold(self, actor: Actor, value: str) -> Decision: ...
    def source_within(self, value: str, source: str) -> bool: ...                 # synchronous: no I/O

class Bound(BaseModel):  updates: dict[str, list[str]]
class NeedsInput(BaseModel):  reply: str
class ScopeBinder(Protocol):
    async def bind(self, actor: Actor, session: SessionState, text: str, spec: AgentSpec) -> Bound | NeedsInput: ...

class SpecResolver(Protocol):                        # which spec runs this turn
    async def resolve(self, actor: Actor, session: SessionState, text: str,
                      requested: AgentSpec | None) -> AgentSpec | NeedsInput: ...
```

| Rule | Requirement |
|------|-------------|
| One decision point | Every model call, tool call, retrieval, engagement, and context widening MUST be decided by `Policy.authorize`, and every decision, allowed or denied, MUST be recorded. |
| Default | The dummy `AllowAll` policy ships; an application that sets no policy gets it and is told so in the package documentation. |
| Fail closed | A `Policy` that raises is a denial with reason `policy_unavailable`. |
| Scope required | A call whose `requires` keys are not all held MUST be denied by the policy with `scope_required`. |
| Resolution | The engine calls `SpecResolver.resolve` once per turn, before binding, with the spec the caller named or `None`. `NeedsInput` ends the turn like a binder's. A resolver that raises ends the turn with a generic reply and no model call. |
| Binding | The engine calls `ScopeBinder.bind` once per turn before any model call or tool call. `NeedsInput` ends the turn with stop reason `scope_required` and the reply as an assistant message; no model call is made. A binder that raises ends the turn the same way with a generic reply. |
| Fixed keys | Once a model call is authorized the engine copies the held values into `bound`; a `fixed` key's bound values MUST NOT change afterward. |
| Kinds | A spec naming a `ContextKey` kind no `ScopeKind` is registered for MUST be refused at load. |

### 3. `SessionState`, what the engine reads of a session

```python
class SessionState(BaseModel):                       # loaded by the store when the turn lease is taken
    id: str; owner: str; messages: tuple[Message, ...]
    context: dict[str, list[str]] = {}; bound: dict[str, list[str]] = {}
    model: str = "auto"; effort: str = "auto"; approval: Literal["auto", "manual"] = "auto"
    disabled_tools: tuple[str, ...] = (); pending_confirmation: PendingConfirmation | None = None
    engaged: tuple[str, ...] = (); pinned: dict[str, AgentSpec] = {}
    flags: dict[str, JsonValue] = {}                 # application-defined, e.g. an entitlement outage seen this turn
```

The state is a snapshot of the log at the lease (CONTRACT-003). The engine changes a session only by appending entries through the `TurnLog`; it never mutates `SessionState` in place and never persists it as a document.

### 4. Tools and the provider surface

```python
class ToolContext(Protocol):
    scope: dict[str, tuple[str, ...]]; actor: Actor
    def capability(self, kind: type[T]) -> T: ...    # raises CapabilityUnavailable
    async def read(self, resource: str, fetch: Callable[[], T | Awaitable[T]], *, value: str | None = None) -> T: ...

class Tool(Protocol):
    name: str; description: str; input_model: type[BaseModel]
    requires: frozenset[str]                         # scope keys the call needs held
    model_visible: bool; always_ask: bool
    replay: Literal["safe", "unsafe"]                # may the engine rerun it after an interrupted turn
    outside_content: bool                            # its result is outside content, scanned as such
    definition_hash: str | None                      # set for a connected tool; a changed hash refuses the call
    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool: ...                       # synchronous: no I/O
    def run(self, ctx: ToolContext, args: BaseModel) -> str | Awaitable[str]: ...               # def or async def
    def drawn_from(self, output: str) -> dict[str, tuple[str, ...]]: ...                       # synchronous: context key -> sources

class Provider(Protocol):                            # every method has a no-op default in `BaseProvider`; each MAY be def or async def
    id: str                                          # a slug, unique in the registry
    contract: int                                    # the contract version it was written for
    def tools(self) -> Sequence[Tool]: ...
    async def capabilities(self, actor: Actor, session: SessionState) -> Mapping[type, object]: ...
    async def prompt_section(self, session: SessionState, spec: AgentSpec) -> str | None: ...
    async def hidden_tools(self, session: SessionState, spec: AgentSpec) -> frozenset[str]: ...
    async def before_request(self, session: SessionState, spec: AgentSpec,
                             messages: tuple[Message, ...]) -> tuple[Message, ...]: ...
    async def before_call(self, session: SessionState, spec: AgentSpec, call: ToolCall) -> Refusal | None: ...
    async def after_call(self, session: SessionState, spec: AgentSpec, call: ToolCall, message: Message) -> Message: ...
    async def after_answer(self, session: SessionState, spec: AgentSpec, answer: str) -> str | None: ...   # a suggested next message

class Refusal(BaseModel):  reason: str               # "x.<provider id>.<reason>"
```

A function tool: the library also offers a decorator that builds a `Tool` from a typed function, deriving `input_model` and the JSON schema from its signature; `@tool` is sugar over this protocol and adds no rules.

| Rule | Requirement |
|------|-------------|
| Plain functions | A tool's `run` or a provider method written as a plain `def` MUST be run by the engine in a worker thread, with the caller's context variables copied and a bounded concurrency limiter, so it cannot block the event loop. A function written as `async def` runs on the loop and MUST NOT block. |
| Gated reads | A tool reads data only through `ToolContext.read`. The engine authorizes `Action.RETRIEVE` for `resource`, records the decision, opens the retrieval span, then calls `fetch` (awaiting it if it is awaitable); a denial raises `RetrievalDenied(reason)` to the tool. |
| Capabilities | `ToolContext.capability` returns what a provider put on the context for this call. A kind no provider supplied raises `CapabilityUnavailable`, which the engine reports to the model as a typed tool error. A second provider MUST NOT add a field to the engine's types. |
| Narrow only | `before_call` returns a `Refusal` or `None`; `None` means no objection and never an allow. `hidden_tools` and `before_request` MAY remove or redact; no provider method returns an authorization decision. |
| After a guard | `after_call` runs after the result guards. If the returned message's `content` differs from the one passed in, the engine MUST re-run every result guard on the new content. |
| Suggestion | `after_answer` runs once when a turn ends in the model's answer. Its return value is a suggestion shown beside the reply and never part of the log; an exception or a slow call yields no suggestion and never changes how the turn ends. |
| Fail closed | A provider method that raises: `before_call` is a refusal with reason `x.<id>.provider_error`; `after_call` replaces the content with a withheld notice; `before_request`, `prompt_section`, `hidden_tools`, and `capabilities` end the step as `provider_error`. The exception is logged by class name only. |
| Registry | Provider ids are unique; tool names are unique across providers; a clash is refused at load with `provider_conflict`. |
| No new gate | A provider cannot register a model client, a session store, an audit sink, a loop, or a way to run a tool outside the engine's pipeline. |

### 5. Result guards

```python
class GuardInput(BaseModel):  tool: str; output: str; outside_content: bool; drawn_from: dict[str, tuple[str, ...]]
class GuardVerdict(BaseModel):  outcome: Literal["ok", "withheld", "denied"]; reason: str | None = None; facts: dict[str, JsonValue] = {}
class ResultGuard(Protocol):
    id: str
    async def check(self, item: GuardInput, session: SessionState) -> GuardVerdict: ...
```

| Rule | Requirement |
|------|-------------|
| Built-in guards | The engine always runs, in this order: the bound-context floor (every source in `drawn_from` MUST be within the session's bound values for its key, by the key's `ScopeKind`), then an injection scan through a scanner port with a rules-based default. An application MAY append guards; it MUST NOT remove or reorder the built-in two. |
| Fail closed | A guard that raises withholds the result with reason `<id>_unavailable`. |
| Withheld results | A withheld result reaches the model as a typed denial and never as content; the audit entry names the call and reason and, for a denied or unvalidated call, never its argument values. |

### 6. Telemetry

```python
class AuditContext(BaseModel):  actor: str; session_id: str | None; trace_id: str | None; span_id: str | None; spec_id: str | None; spec_version: str | None
class AuditSink(Protocol):
    async def emit(self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]) -> None: ...
class Tracer(Protocol): ...                          # synchronous: start_as_current_span, current_ids, set_current_attribute
```

| Rule | Requirement |
|------|-------------|
| Audit kinds | The closed set `session_started`, `user_message`, `assistant_message`, `model_call`, `tool_call`, `authorization`, `confirmation`, `retrieval`, `turn_ended`, `turn_cancelled`, `turn_interrupted`, `session_settings`, `session_closed`, plus `x.<provider id>.<kind>`. One entry per step and one per gate decision. |
| Spans | `tether.turn` (root), `tether.model_call`, `tether.tool_call`, `tether.gate`, and `tether.retrieval`, with attributes in the `tether.` namespace carrying identifiers, sizes, tiers, token counts, and reasons. The prefix MAY be configured; a deployment keeps it stable. Spans carry across awaits through context variables. |
| Content | Spans and audit entries MUST NOT carry message or tool content, argument values of a denied or invalid call, or a credential. Content capture is a separate, opt-in provider and is off by default. |
| Usage | A model call's token usage is recorded on the span, the audit entry, and the session log (CONTRACT-003). |
| Defaults | `NullTracer` and `ListAuditSink` ship; an OpenTelemetry adapter ships as an extra. |

### 7. The gate sequence and its commit points

The engine runs these steps in this order. A provider relies on the order and cannot change it. Every `await` is a cancellation point. A step's entries are appended in one `TurnLog.append` call (CONTRACT-003), so each step is committed before the next starts.

**Turn start**: (1) Append `turn_started` and the user message. (2) `SpecResolver.resolve`. (3) `ScopeBinder.bind`; `NeedsInput` appends the reply and ends the turn `scope_required`.

**A model step**: (1) `Policy.authorize(CALL_MODEL)` and the held-scope check; deny ends the turn `model_denied`. (2) Copy held values into `bound`. (3) Provider `prompt_section`, `hidden_tools`, `before_request`. (4) Call the model port in its own span; an outage ends the turn `model_unavailable`. (5) Append the reply message and the model-call facts.

**A tool call**: (1) `Policy.authorize(CALL_TOOL)` with `requires` and `permitted`; deny returns a typed denial. (2) A connected tool's `definition_hash` MUST equal the pinned hash, else `definition_changed`. (3) Provider `before_call`; a refusal is recorded as a denial. (4) Validate arguments against `input_model`. (5) If approval is manual or `always_ask` and `asks_first`, append the confirmation request and end the turn `confirmation_required`. (6) Run the tool in its own span; failures are typed results. (7) Widen growing keys the result declares, then hold the result to the bound context. (8) Run the result guards. (9) Provider `after_call`, re-guarding changed content. (10) Append the tool-result message and the tool-call facts.

**Turn end**: append `turn_ended` with the stop reason. A cancelled turn appends `turn_ended` with `cancelled` in a shielded cleanup; recovery appends `interrupted` for a turn whose lease expired.

### 8. Interaction (in-process drivers)

```python
class Answer(BaseModel):  approved: bool; note: str | None = None
class Interaction(Protocol):                         # for drivers that answer in-process: tests, the CLI, scripts
    async def decide(self, pending: PendingConfirmation) -> Answer: ...

async def drive(client: Client, session_id: str, text: str, interaction: Interaction, **options) -> TurnResult: ...
```

`drive` sends a message through the client API (CONTRACT-003), and while the turn stops for confirmation asks `interaction`, answers, and continues. Dummies: `AutoApprove`, `AutoDecline`, `ScriptedAnswers(list)`, and `ConsoleInteraction`. A server adapter exposes confirmation as its own route and does not use `Interaction`.

### 9. Conformance suites

The package ships `tether.testing.conformance`, as asynchronous tests run with the `anyio` pytest plugin: `policy_suite(factory)` (fail closed, scope required, one decision per action), `audit_sink_suite`, `tracer_suite`, `provider_suite(factory)` (narrow-only, fail closed, registry conflicts, a plain `def` hook does not block the loop), and `guard_suite(factory)`. An implementation of a port is conformant when it passes its suite; an application runs each suite against its own implementation. The model port and session store suites are in CONTRACT-001 and CONTRACT-003.

## Precedence and Compatibility

- Versioning: one integer, `ENGINE_CONTRACT_VERSION`. A provider declares the version it was written for in `Provider.contract`; the registry refuses a provider above the engine's and accepts a lower one the engine still supports. `AgentSpec.schema_version` versions the stored spec separately.
- Ordering or precedence: the gate sequence of section 7 is normative and overrides any provider's expectation.
- Backward-compatibility rules: adding an optional method with a default to `Provider`, a new `Action` the engine does not itself raise, or a new optional field is compatible. Changing a signature, adding a reason the engine reads, reordering the sequence, or tightening a rule is a new version. There is no synchronous form, so asynchrony is not a compatibility axis.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| `ModelUnavailable` | Turn ends `model_unavailable` with the reason and the provider's request id | The user may resend | The log names the failed call |
| `Policy` raises | Denial `policy_unavailable` | No | Restore the policy source |
| `ScopeBinder` or `SpecResolver` raises | Turn ends `scope_required`, generic reply | No | Restore it |
| Provider method raises | Section 4 "Fail closed" | No | Fix the provider |
| `ResultGuard` raises | Result withheld, `<id>_unavailable` | No | Restore the guard |
| `EventSink` raises | Event dropped, turn unaffected | No | The consumer reconnects and reads the log |
| Turn cancelled | Steps already committed remain; `turn_ended: cancelled` appended; cancellation propagates | The person may resend | The log is consistent |
| The lease is lost during a turn | The engine stops; its further appends fail with `LeaseLost` | No | Recovery resolves the turn |
| A tool raises | Typed `tool_error` result to the model; audit entry; turn continues | The model may retry | None |
| A tool or hook blocks the loop | Not detectable by the engine; a plain `def` is offloaded; an `async def` that blocks is a defect found by the `ASYNC` lint rules and review | n/a | Fix the code |

## Examples

```python
# An application composes the ports (the only place adapters are imported):
engine = Engine(
    model=PydanticAIModel(settings.model),           # ADR-002
    policy=AllowAll(), binder=NoScope(), resolver=FixedSpec(SPEC), kinds={},
    providers=[NotesProvider()], guards=[],          # built-in guards always run
    audit=ListAuditSink(), tracer=NullTracer(), clock=SystemClock(), ids=UuidIds(),
)
```

```python
# A tool as a plain function:
@tool(requires=(), always_ask=False)
def lookup(ctx: ToolContext, q: str) -> str:
    return f"result for {q}"
```

## Non-Normative Notes

- The provider surface is the extension point for features an application layers on the loop (stages, skills, attachments, document access). The first downstream application's features are the test that it is neither too narrow nor too wide.
- Running independent tool calls of one reply in parallel is not part of v1. It changes the order of log entries and audit records and is a decision of its own.
- Context management for long sessions (compaction) is out of v1; `before_request` is the hook an application can use today to bound the messages it sends.

## Open Items

| Item | Owner |
|------|-------|
| Whether `Workflow`-like declarations from applications need a shared, versioned base package if providers repeatedly need the same fields | Erik LaBianca |
| The exact injection-scan port shape (the scanner's interface) | Erik LaBianca, in the telemetry and guards design |
