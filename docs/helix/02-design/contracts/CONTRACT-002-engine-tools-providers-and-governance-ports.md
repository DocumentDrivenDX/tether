---
ddx:
  id: CONTRACT-002
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-24
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: ADR-010
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-014
      kind: informed_by
    - id: ADR-015
      kind: informed_by
    - id: ADR-016
      kind: informed_by
    - id: ADR-017
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: ADR-021
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
    - id: architecture
      kind: informed_by
---

# CONTRACT-002: Engine, Tools, Providers, and Governance Ports

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-002
**Type**: library (Python ports)
**Version**: v2
**Related**: ADR-001, ADR-003, ADR-005, ADR-010, ADR-011, ADR-014, ADR-015, ADR-016, ADR-017, ADR-018, CONTRACT-001, CONTRACT-003

## Purpose

This contract fixes what an application implements to run the engine and what a provider implements to contribute a feature to it. It defines the turn the engine runs, the tool interface, the governance and telemetry ports, the order in which the engine runs its gates (because that order is what a provider may rely on and never change), the lifecycle of an approval, the execution state of a tool invocation, and the planner that resumes an interrupted turn. It also states the trust model: what the engine enforces and what extension authors must do. The interface is asynchronous throughout and has no synchronous form (ADR-001).

## Scope and Boundaries

- In scope: identity, decisions, and the specification the loop reads; the engine's entry points; the policy, scope-binding, and specification-resolution ports; the tool and provider ports; the result-guard port; the audit sink and tracer; the trust model; the gate sequence and its commit points; the approval lifecycle; execution states and the resume planner; the in-process interaction port; cancellation and failure behavior; versioning.
- Out of scope: the model port (CONTRACT-001); the session log, stores, archive, and client API (CONTRACT-003); an application's manifest format, which the application compiles to an `AgentSpec`; scope kinds, the injection heuristic, and suggestions, which are optional packages or the application's (ADR-017); the wire shapes of any server adapter.
- Owning system or team: tether core. A new port, a changed signature, or a reason the engine reads is a contract version change and an ADR-003 amendment, justified by a second application or a feature a provider cannot express.

## Normative Surface

Signatures are Python 3.11. Payload types are frozen `pydantic` models with `extra="forbid"`; ports are `typing.Protocol` classes. MUST, MUST NOT, SHOULD, and MAY are used as defined in RFC 2119.

**Asynchronous form.** A method shown `async def` is a coroutine function. A port that does no I/O is synchronous and is marked so: `Tracer`, `Clock`, `Ids`, and `Tool.asks_first`. A tool or a provider method MAY be written as a plain `def`; the engine then runs it in a worker thread (section 4).

### 1. Identity, decisions, the spec, and the turn

```python
class Actor(BaseModel):                                                          # who a call is for
    id: str; kind: str = "user"; attributes: dict[str, JsonValue] = {}
    principal: str | None = None  # verified model attribution; defaults to id, never a credential
    credential: Credential | None = Field(default=None, exclude=True, repr=False)   # CONTRACT-001; never stored (ADR-011)

# Actions are strings. The core defines these; an optional package defines its own as `x.<package>.<action>`.
CALL_MODEL = "call_model"; CALL_TOOL = "call_tool"; RETRIEVE = "retrieve"
APPROVE = "approve"; RESUME = "resume"; READ_SESSION = "read_session"
RESOLVE_EFFECT = "resolve_effect"; READ_CALL_PAYLOAD = "read_call_payload"

# Actor.id remains the session owner and policy/approval subject. Model RequestTags.principal
# is Actor.principal when supplied, otherwise Actor.id. The application verifies the mapping;
# neither field selects a credential or grants access. Empty supplied principal is refused.

class Decision(BaseModel):  allowed: bool; reason: str                          # "allowed" when allowed
class AgentSpec(BaseModel):                                                      # what the loop reads of a task
    schema_version: int = 1
    id: str; version: str; title: str; instructions: str; model: str            # model is a tier
    tools: tuple[str, ...] = (); max_steps: int = 20
    ext: dict[str, JsonValue] = {}                                               # blocks the engine passes untouched to providers, by namespace
class PendingConfirmation(BaseModel):
    invocation_id: str; tool: str; args_digest: str; tool_digest: str; spec_digest: str
    arguments: dict[str, JsonValue] | None = None  # hydrated only after authorized history read
    description: str; history_ref: HistoryRef
    history_status: Literal["not_loaded"] | HistoryStatus = "not_loaded"
    requested_at: datetime; expires_at: datetime | None = None
StopReason = Literal["final", "step_limit", "model_denied", "model_unavailable",
                     "scope_required", "confirmation_required", "cancelled", "interrupted",
                     "context_unavailable", "model_incomplete", "effect_blocked", "provider_error"]
class TurnResult(BaseModel):
    turn_id: str; stop_reason: StopReason; reason: str | None = None            # the outage's short cause
    messages: tuple[Message, ...] = ()

class Engine:                                                                    # constructed with the ports; no environment reads
    async def run_turn(self, actor: Actor, log: TurnLog, text: str, *, spec: AgentSpec | None = None,
                       attachments: tuple[AttachmentRef, ...] = (), events: EventSink | None = None,
                       parent: object | None = None) -> TurnResult: ...
    async def answer(self, actor: Actor, log: TurnLog, invocation_id: str, answer: Answer, *,
                     events: EventSink | None = None, parent: object | None = None) -> TurnResult | None: ...
    async def inspect_pending(self, actor: Actor, session: SessionState, source: Entry,
                              request: Entry, proposal: Message) -> PendingConfirmation: ...
    async def aclose(self) -> bool: ...             # bounded, seals submissions; false if work remains
    async def wait_closed(self) -> None: ...         # explicit actual-exit wait after close; caller may bound it
    async def resume(self, actor: Actor, log: TurnLog, *, events: EventSink | None = None,
                     parent: object | None = None) -> TurnResult: ...
```

`TurnLog` is the live store's write handle for one turn (CONTRACT-003). `Engine.answer` returns `None` when `invocation_id` is not the pending approval or another answer already won, and changes nothing. The engine records denied barrier checks as `authorization` facts with reason `effect_unresolved`; they cannot be overridden by a policy result. `AuthzRequest.resource` for a model call is `model:<tier>` and `purpose` distinguishes answer from summary. The engine decides exclusively from `Decision.allowed`; `reason` is an allow-listed metadata code explaining that decision. An unrecognized or content-bearing reason is normalized to `policy_reason_invalid` without overriding a denial or logging the original text. Neither a reason string nor a provider result can grant authorization. A provider's reason MUST be `x.<provider id>.<reason>`.

`inspect_pending` is read-only: current READ_SESSION, original manifest/proposal identity,
current tool/schema/spec digests and exact normalized executable arguments are checked using
the same validator as restart reconstruction. It does not acquire an execution approval,
dispatch a tool, call a model or append read facts to the journal. The service first verifies
the reachable source record and hash; unavailable content produces unhydrated pending metadata.
A displayed value never replaces confirmation's fresh gates. Inspection failures expose only
short metadata codes. `wait_closed` preserves actual worker lifetime for resource cleanup;
it is not permission for another invocation or a synchronous facade.

The engine owns execution semantics, tool registration and resolution by name. Its implementation uses Pydantic AI Agent through the internal runtime integration selected in ADR-003. Engine is the Tether invariant facade, not a second model/tool loop; public Agent hooks and Tether-owned callbacks preserve these gates and authoritative state boundaries. SDK capability composition is fixed/trusted and SDK types stay internal. Finding tools or workflows for the model, engagement and routing, the specification a session is pinned to, stages, skills, and suggestions are the application's or a provider's, and read what they need from `AgentSpec.ext`. The `turn_started` entry records the specification's id, version, and digest, and a resolver may pin it (ADR-017, ADR-018).

### 2. Policy, scope binding, and spec resolution

```python
class UsageSoFar(BaseModel):
    steps: int; input_tokens: int; output_tokens: int; elapsed_seconds: float
    model_calls: int; summary_calls: int; usage_complete: bool  # false when any call lacks usage
class AuthzRequest(BaseModel):
    actor: Actor; action: str; resource: str
    held: dict[str, tuple[str, ...]] = {}            # the session's scope values, an opaque map to the core
    requires: frozenset[str] = frozenset()           # keys the call needs held, an opaque set to the core
    permitted: frozenset[str] | None = None          # names the caller may use, when the action has a list
    purpose: Literal["answer", "summary", "correction"] | None = None
    usage: UsageSoFar | None = None                  # what the turn has used so far, so a policy can enforce a budget

class Policy(Protocol):
    async def authorize(self, request: AuthzRequest) -> Decision: ...

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
| One decision point | Every model call, tool call, retrieval, approval, and resume MUST be decided by `Policy.authorize`, and every decision, allowed or denied, MUST be recorded. |
| Default | The dummy `AllowAll` policy ships; an application that sets no policy gets it and is told so in the package documentation. |
| Fail closed | A `Policy` that raises is a denial with reason `policy_unavailable`. |
| Opaque scope | The core passes `held` and `requires` to the policy and records its decision; it gives them no further meaning. Fixed and growing keys, scope kinds, and the bound-versus-held distinction are the optional `tether.scope` package (ADR-017). |
| Budgets | A policy MAY deny `CALL_MODEL` with `budget_exceeded` from `usage`; missing provider usage sets `usage_complete=false`, never zero certainty. Every summary call has a separate authorization and usage record. The core enforces `AgentSpec.max_steps` and the configured request/context/worker/deadline bounds in CONTRACT-001/004 and section 4. Cumulative turn token, wall-time and cost thresholds remain policy decisions, with values supplied by the application. |
| Resolution | The engine calls `SpecResolver.resolve` once per turn, before binding, with the spec the caller named or `None`. `NeedsInput` ends the turn like a binder's. A resolver that raises ends the turn with a generic reply and no model call. |
| Binding | The engine calls `ScopeBinder.bind` once per turn before any model call or tool call, records its updates in a `settings` entry, and rebuilds `held` from the log. `NeedsInput` ends the turn with stop reason `scope_required` and the reply as an assistant message; no model call is made. A binder that raises ends the turn the same way with a generic reply. |

### 3. `SessionState`, what the engine reads of a session

```python
class SessionState(BaseModel):                       # loaded by the store when the turn lease is taken
    id: str; owner: str; history_refs: tuple[HistoryRef, ...]
    held: dict[str, list[str]] = {}                  # rebuilt from the log; opaque to the core
    model: str = "auto"; effort: str = "auto"; approval: Literal["auto", "manual"] = "auto"
    through_index: int; unresolved_effects: tuple[str, ...]  # invocation ids, rebuilt from intents/results/resolutions
    disabled_tools: tuple[str, ...] = (); pending: PendingConfirmation | None = None
```

CONTRACT-004 defines the separate model context view and its ports. `SessionState.history_refs` identifies committed source content through CONTRACT-005. Before context or tool-argument use the engine resolves only required refs under the present actor and configured bounds. Missing required content stops explicitly. Execution state derives solely from journal facts, not summaries or gateway rows. The state is a metadata/reference snapshot of the execution journal at the lease (CONTRACT-003). The engine changes a session only by appending entries through the `TurnLog`; it never mutates `SessionState` in place and never persists it as a document. An application's own data is not in this type (ADR-018); per-turn application data travels in `Actor.attributes` or a capability.

### 4. Tools and the provider surface

```python
class ToolOutput(BaseModel):  text: str; attachments: tuple[AttachmentRef, ...] = (); facts: dict[str, JsonValue] = {}

class ToolContext(Protocol):
    scope: dict[str, tuple[str, ...]]; actor: Actor; invocation_id: str
    def capability(self, kind: type[T]) -> T: ...    # raises CapabilityUnavailable
    async def read(self, resource: str, fetch: Callable[[], T | Awaitable[T]], *, value: str | None = None) -> T: ...

class Tool(Protocol):
    name: str; version: str; description: str; input_model: type[BaseModel]
    requires: frozenset[str]                         # scope keys the call needs held; opaque to the core
    model_visible: bool; always_ask: bool
    effect: Literal["read_only", "effectful"]           # default effectful; independent of replay
    replay: Literal["safe", "unsafe"]                # default "unsafe"; the author's declaration (section 10)
    timeout_seconds: float | None                    # enforced by the engine; None means no limit set by the tool
    traits: dict[str, JsonValue]                     # declarative metadata guards may read, such as {"outside_content": true}
    definition_hash: str | None                      # set for a connected tool; a changed hash refuses the call
    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool: ...                       # synchronous: no I/O
    def run(self, ctx: ToolContext, args: BaseModel) -> str | ToolOutput | Awaitable[str | ToolOutput]: ...   # def or async def
    async def reconcile(self, ctx: ToolContext, args: BaseModel, invocation_id: str) -> Reconciled: ...   # optional read-only reconciliation

Reconciled = Completed | NotCompleted | Unknown        # Completed carries the tool's output

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

class Refusal(BaseModel):  reason: str               # "x.<provider id>.<reason>"
```

**Argument normalization and approval identity.** Preserve the original model proposal in authoritative source history. `CallManifest.args_digest` binds the canonical raw JSON proposal; it is not an approval. At gate (3), validate/normalize it against the pinned `input_model` into canonical JSON executable values. Approval and intent `args_digest` bind those executable values; `tool_digest` already binds the tool version and schema. All argument-sensitive gates/hooks, asks_first, approval presentation and execution use the same normalized values. Show those values for approval and execute the corresponding validated object without further coercion, defaulting or mutation. Trusted validators must be pure, bounded and deterministic; validation cannot access the network or execute a tool. Hook copies cannot mutate the bound validated object. A wrapper must not change those values after binding. Subsequent change re-enters validation, current authorization and approval with a new binding; an earlier approval cannot transfer. On recovery verify the raw source against the manifest, revalidate under the identical tool/schema version, and require the resulting executable digest to equal the original approval/intent digest before dispatch. Non-JSON values require an explicit lossless, versioned tool codec or rejection before approval. Invalid arguments remain raw proposal facts and produce a typed error, never an executable approval. Qualification covers defaults, coercions, validators and schema changes through each candidate runner.

A function tool: the library also offers a decorator that builds a `Tool` from a typed function, deriving `input_model` and the JSON schema from its signature; `@tool` is sugar over this protocol and adds no rules. A tool that returns a `str` is the same as one that returns `ToolOutput(text=...)`.

| Rule | Requirement |
|------|-------------|
| Worker lifetime | An offloaded plain function owns a bounded executor permit until the function actually exits, including after the awaiting task is cancelled, timed out or loses its lease. The executor tracks actual running workers with completion callbacks; abandoning a wait MUST NOT release that permit. Queued work not yet started is cancelled without an effect. A live worker keeps a process-local session-busy marker; new turns and resolution for that session raise `WorkerOutstanding` until it exits. |
| Plain functions | A tool's `run` or a provider method written as a plain `def` MUST be run by the engine in a worker thread, with the caller's context variables copied and a bounded concurrency limiter, so it cannot block the event loop. A function written as `async def` runs on the loop and MUST NOT block. |
| Mediated reads | A tool SHOULD read data through `ToolContext.read`. The engine authorizes `RETRIEVE` for `resource`, records the decision, opens the retrieval span, then calls `fetch` (awaiting it if it is awaitable); a denial raises `RetrievalDenied(reason)` to the tool. The engine guarantees this for reads that go through `read`; it cannot guarantee that a tool uses it (section 7). |
| Capabilities | `ToolContext.capability` returns what a provider put on the context for this call. A kind no provider supplied raises `CapabilityUnavailable`, which the engine reports to the model as a typed tool error. A second provider MUST NOT add a field to the engine's types. |
| Order | Providers run in registration order. |
| `before_request` | Hooks chain: each receives the previous hook's output. The result MUST be a tuple of messages that is a structural subset of the input: the same or fewer messages, unchanged roles, unchanged tool-call ids/names/arguments, no added tool calls, and attachments that are a subset. An output that is not is a `provider_output_invalid` failure of the step. Whether a change to a message's content is a faithful redaction is the author's obligation. |
| `before_call` | Every provider's hook runs in order. It returns a `Refusal` or `None`; `None` means no objection and never an allow. The first refusal wins and is recorded with its provider id. |
| `after_call` | Hooks chain. The returned message MUST keep `role`, `tool_call_id`, and `is_error`; a change to any of them is `provider_output_invalid` and the result is withheld. If the final content differs from what the guards saw, the engine MUST re-run every guard on the final content. |
| Reasons | A reason without the `x.<provider id>.` prefix is normalized to `x.<provider id>.invalid_reason`. |
| `prompt_section` and `hidden_tools` | `prompt_section` adds text to the instructions; it is not an authorization and the contract does not treat it as one. `hidden_tools` removes tools from what the model is offered. |
| Time limit | A hook that exceeds the engine's configured time limit fails closed. The value is the application's. |
| Fail closed | A provider method that raises: `before_call` is a refusal with reason `x.<id>.provider_error`; `after_call` replaces the content with a withheld notice; `before_request`, `prompt_section`, `hidden_tools`, and `capabilities` end the step as `provider_error`. The exception is logged by class name only. |
| Registry | Provider ids are unique; tool names are unique across providers; a clash is refused at load with `provider_conflict`. |
| Tool version | `Tool.version` is a non-empty application-supplied semantic version string; its bytes enter approval/definition digests. A changed spec/tool digest invalidates the pending approval before dispatch. Runtime introspection is not evidence of implementation equivalence. |
| No new gate | A provider cannot register a model client, a session store, an audit sink, a loop, or a way to run a tool outside the engine's pipeline. |

### 5. Result guards

```python
class GuardInput(BaseModel):  tool: str; output: str; traits: dict[str, JsonValue]; facts: dict[str, JsonValue]
class GuardVerdict(BaseModel):  outcome: Literal["ok", "withheld", "denied"]; reason: str | None = None; facts: dict[str, JsonValue] = {}
class ResultGuard(Protocol):
    id: str
    async def check(self, item: GuardInput, session: SessionState) -> GuardVerdict: ...
```

| Rule | Requirement |
|------|-------------|
| No mandatory guard | The engine runs the guards it is given, in order. The default list is empty. The optional `tether.guards` package ships a rules-based injection heuristic and `default_guards()`; the optional `tether.scope` package ships the bound-context guard (ADR-017). |
| Not a security boundary | A guard is a heuristic or a check the application chose. The documents MUST NOT describe the injection heuristic as protection against prompt injection; policy, scope, and approval are the controls. |
| Fail closed | A guard that raises withholds the result with reason `<id>_unavailable`. |
| Withheld results | A withheld result reaches the model as a typed denial and never as content; the audit entry names the call and reason and, for a denied or unvalidated call, never its argument values. |

### 6. Telemetry

```python
TraceScalar = str | int | float | bool
class TraceIds(BaseModel):
    trace_id: str                         # 32 lowercase hex, nonzero
    span_id: str                          # 16 lowercase hex, nonzero
    trace_flags: int = 0                  # strict uint8, actual trace flags
class AuditContext(BaseModel):
    actor: str                           # owner / policy subject, never a credential
    event_id: str                        # stable opaque observational identity
    principal: str | None = None          # current effective model principal
    session_id: str | None = None
    trace_id: str | None = None           # paired real context, same rules as TraceIds
    span_id: str | None = None
    spec_id: str | None = None
    spec_version: str | None = None
class AuditSink(Protocol):
    async def emit(self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]) -> None: ...
class Span(Protocol):
    @property
    def ids(self) -> TraceIds | None: ...
    @property
    def traceparent(self) -> str | None: ...
    def set_attribute(self, name: str, value: TraceScalar) -> None: ...
    def error(self, reason: str) -> None: ...
class Tracer(Protocol):
    def start_as_current_span(self, name: str, *,
                              attributes: Mapping[str, TraceScalar] | None = None
                              ) -> AbstractContextManager[Span]: ...
    def current_ids(self) -> TraceIds | None: ...
    def set_current_attribute(self, name: str, value: TraceScalar) -> None: ...

```

| Rule | Requirement |
|------|-------------|
| Audit kinds | Context preparation uses namespaced `x.context.prepared`, `x.context.failed`, and `x.context.compaction` facts with identifiers/counts only. Resolution is an `authorization` plus namespaced `x.effects.resolved` fact; worker abandonment/exit uses `x.executor.outstanding`/`x.executor.exited` with ids and counts. The closed core set `session_started`, `user_message`, `assistant_message`, `model_call`, `tool_call`, `authorization`, `confirmation`, `retrieval`, `turn_ended`, `turn_cancelled`, `turn_interrupted`, `turn_resumed`, `session_settings`, `session_closed`, plus `x.<provider id>.<kind>`. One entry per step and one per gate decision. |
| Spans | `tether.turn` (root), `tether.model_call`, `tether.tool_call`, `tether.gate`, and `tether.retrieval`, with attributes in the `tether.` namespace carrying identifiers, sizes, tiers, token counts, and reasons. The prefix MAY be configured; a deployment keeps it stable. Spans carry across awaits through context variables. |
| Content | Spans and audit entries MUST NOT carry message or tool content, argument values of a denied or invalid call, or a credential. Content capture is a separate, opt-in provider and is off by default. |
| Usage | A model call's token usage is recorded on the span, the audit entry, and the session log (CONTRACT-003). |
| Written by the engine | Only the engine writes audit entries and spans for engine decisions; an extension's own audit entries use its namespace. |
| Defaults | `NullTracer` and `NoopAuditSink` perform no I/O and retain no records. Bounded `ListAuditSink` and explicitly configured recording trace doubles ship in the test kit. An OpenTelemetry adapter remains a separately qualified extra. |

### 7. The trust model

Python tools and providers are trusted application code (ADR-016). They run in the application's process with its privileges. Tether is not a sandbox.

| Enforced by the engine | Obligations of the extension author (not enforced) |
|------------------------|----------------------------------------------------|
| The gate sequence runs, in order, on every call | Read data through `ToolContext.read`; do no independent network, file, or process I/O the application did not intend |
| A provider's return value has the type and shape of section 4, or the step fails closed | Redact faithfully in `before_request`; do not disclose `ctx.actor.credential` |
| A hook, guard, or policy that raises is a refusal | Declare `effect`, `replay`, `requires`, and `traits` honestly |
| Policy, approval, and the guards the application configured run on every call | Do not block the event loop; bound runtime |
| Reads through `read` are authorized and recorded | Make downstream calls idempotent with the invocation id where possible (ADR-014) |
| Audit entries and spans are written by the engine and carry no content | |
| A credential is written to no entry, span, audit record, or cassette | |

Isolating code the application does not trust is outside the core: run it in a subprocess or a sandbox behind a tool, or behind the P2 MCP adapter.

### 8. The gate sequence and its commit points

The engine runs these steps in this order. A provider relies on the order and cannot change it. Every `await` is a cancellation point. A step's entries are appended in one `TurnLog.append` call, so each step is committed before the next starts.

**Turn start**: (1) Persist the accepted user message through CONTRACT-005, then atomically append `turn_started` and its content reference (key `request:<id>` when the caller gave a request id). (2) `SpecResolver.resolve`. (3) `ScopeBinder.bind`; `NeedsInput` appends the reply and ends the turn `scope_required`.

**A model step**: (1) Check the step limit; `Policy.authorize(CALL_MODEL)` for purpose `answer` with `usage`; deny ends `model_denied`. (2) Compose provider prompt sections, tool visibility and source redactions; derive a source-addressed context plan, authorize/execute any bounded summary requests, validate full payload/output budgets, and commit provenance under CONTRACT-004. (3) Reauthorize `CALL_MODEL` for `answer` with the updated usage after auxiliary work; then send the bounded request with the current actor credential and engine-bound attempt recorder (CONTRACT-005). Each authorization is recorded. A failed/outage model call appends its metadata-only `model_call(status="error")` fact and terminal reason where storage permits; it never appends partial tool calls. An outage ends `model_unavailable`. `ModelAttemptDenied` from the recorder ends `model_denied`; store/lease/uncertain-commit failures propagate through the journal failure path and never become provider outages or authorize egress. (4) Persist or bind the source reply through CONTRACT-005, then append its reference and `model_call` fact, including purpose and finish reason. Only a complete `stop` without calls ends `final`; only complete `tool_calls` dispatches. Other terminal outcomes end `model_incomplete` and preserve any returned text with that status. A context failure ends `context_unavailable` before the main request. Summary responses never enter the execution loop.

**A tool call**: (0) If any effectful invocation in this session has an unresolved outcome, refuse every new effectful invocation with `effect_unresolved`, even with a different name, arguments or model call id; do not ask for an approval that could bypass it. Only `effect="read_only"` calls/dialogue may continue. A recovery step may rerun an unresolved `replay="safe"` invocation under the SAME invocation id and unchanged digest through every gate; this exception never authorizes a new call id/idempotency key. (1) `Policy.authorize(CALL_TOOL)` with `requires`, `permitted`, and `usage`; deny returns a typed denial. (2) A connected tool's `definition_hash` MUST equal the pinned hash, else `definition_changed`. (3) Validate and normalize arguments against the pinned `input_model` with finite configured byte/time bounds and no I/O or effects. (4) Every provider's `before_call` receives a ToolCall containing those same canonical executable argument values; the first refusal wins and is recorded as a denial. (5) Approval: if approval is manual, or the tool is `always_ask` and `asks_first` returns true, then if no valid approval exists for this invocation, append the `confirmation` request and end the turn `confirmation_required`; if an answer exists, verify it against section 9, else the call is denied `approval_invalid`. (6) For every effectful tool OR tool with `replay="unsafe"`, append `tool_call` with status `started` (key `intent:<invocation_id>`); if the append is rejected, raises `LeaseLost`, or has an uncertain acknowledgment (`CommitUnknown`), the tool does not run. Same-invocation replay-safe recovery MUST reuse its existing committed intent after checking the original manifest, arguments/tool/spec digests and approval; it MUST NOT append that intent key again with a resumed turn id or changed payload. Only a genuinely new invocation gets a new intent. Fresh authorization is still recorded for the recovery attempt. (7) Run the tool in its own span, under its timeout; failures are typed results. (8) Run the guards in order. (9) Provider `after_call`, chained and validated, re-guarding changed content. (10) Persist the tool-result message through CONTRACT-005, then append its reference and the `tool_call` fact with its final status (key `result:<invocation_id>`).

**After an approval answer**: the engine re-enters the tool-call sequence at step (0), including the current effect barrier. The approval satisfies only step (5). Journal pending state is checked before any cached runner. The accepted decision and resumed running projection commit together before continuation. A supplied informational `Answer.note` is bounded history content referenced by the confirmation, not audit text, executable arguments or automatically injected model input (CONTRACT-003). Continued model attempts bind the present actor credential, verified model principal and session context; a retained runner cannot reuse earlier attribution as authority.

**Resume** (section 10): (1) `Policy.authorize(RESUME)`. (2) Append `turn_started` naming the interrupted turn in `resumes`, and the audit entry `turn_resumed`. (3) For each call by its derived state: run a not-started call through the full tool-call sequence; for a started call that is safe, run the sequence again with the same invocation id (the existing approval is used if its digests still match); for a started call that is not safe, append the `interrupted` result with reason `outcome_unknown` and do not run it. (4) Continue with the next model step.

**Transcript closure**: Before a new user turn or resumed model request, seal incomplete prior call groups under the session lease. A started effectful call receives its single interrupted result with `outcome_unknown`; a started read-only/replay-unsafe call receives its single interrupted non-success result without creating an effect barrier. Every other unstarted or cancelled call also receives a typed non-success result. Closure runs after eligible same-invocation recovery work and before the next model request, so it cannot settle a call before the recovery planner is allowed to retry it. This does not execute a tool, invent success, clear an unknown or mutate an earlier message. If closure cannot commit, no new model request runs. Pending approval groups are handled by their answer/supersession lifecycle. A result already present is never duplicated.

**Turn end**: append `turn_ended` with the stop reason. A cancelled turn appends `turn_ended` with `cancelled` in a shielded cleanup; recovery appends `interrupted` for a turn whose lease expired.

### 9. The approval lifecycle

| Rule | Requirement |
|------|-------------|
| Binding | An approval binds to the session, the `invocation_id`, the `args_digest` (a hash of the canonical JSON of the arguments shown), the `tool_digest` (a hash of the tool's name, version, description, input schema, requires, traits, effect/replay declarations, always-ask flag and connected definition hash, computed for every tool), the current specification digest, and the approving actor's id. The credential is never recorded. Tool authors MUST change `Tool.version` when implementation semantics change; the engine cannot infer that from Python source. |
| Single use | One approval covers one invocation. A safe tool rerun on resume uses the same approval; a tool that is not safe is not rerun. |
| Who may approve | `Policy.authorize(APPROVE)` is decided on the answering actor; the default policy lets the session's owner approve. |
| States | `requested`, `approved`, `declined`, `superseded`, `expired`, `invalid`, each a `confirmation` entry. |
| Decline | A decline becomes a denied tool result with reason `declined` and an optional note; the turn continues. |
| Supersession | A new message sent while an approval is pending records each pending call as denied with reason `superseded`, then starts the new turn. A later answer to the old invocation returns `None`. |
| Expiry | If the application sets `approval_ttl`, an answer arriving after `expires_at` (read from the clock port) appends `expired` and nothing runs. No default is set. |
| Concurrent answers | The answer is appended under the key `approval:<invocation_id>`. The store keeps one; the loser gets `None`. Both attempts are audited. |
| Changed permissions | After an answer the sequence re-enters at step (0), so a policy that now denies `CALL_TOOL` denies the call. |
| Changed tool | A digest that no longer matches ends the call as denied with reason `approval_invalid`. |
| Across a restart | The pending state is in the log, and a waiting session holds no lease. The answerer presents a live actor; no credential is stored. |
| After a crash | A crash between the approval and the intent is resumed without asking again, provided the digests and every gate still pass. |

### 10. Execution states and the resume planner

```python
class ResumeTool(BaseModel):  tool_digest: str; replay: Literal["safe", "unsafe"]  # immutable current declaration snapshot
class ResumeStep(BaseModel):  kind: Literal["run", "rerun", "record_interrupted", "model_step"]; call_entry_index: int | None = None; call_position: int | None = None; invocation_id: str | None = None
class ResumePlan(BaseModel):  interrupted_turn_id: str; steps: tuple[ResumeStep, ...]

def plan_resume(entries: Sequence[Entry], tools: Mapping[str, ResumeTool], *, session_id: str) -> ResumePlan: ...   # pure: reads only journal facts/manifests and declarations; returns source positions, never hydrates history
```

| State | The log shows | Resume rule |
|-------|---------------|-------------|
| Not started | The call is in a committed assistant entry; no required intent (effectful or replay-unsafe); no result | Run it fresh through every gate. It is not interrupted. |
| Started | A `tool_call` entry with status `started`; no result | A safe tool is rerun with the same `invocation_id`. Any other tool, and a tool no longer registered, gets a result with status `interrupted` and reason `outcome_unknown`, and is not rerun automatically. |
| Finished | A result entry | Nothing. |
| Resolved | A `resolved` entry from reconciliation or a person | Nothing. |

| Rule | Requirement |
|------|-------------|
| Referenced calls | Message entries carry a content-free call manifest (call position, tool-call id, tool name, pinned tool digest, effect/replay declarations and canonical raw-proposal argument digest). The planner returns source entry/call positions; the engine resolves arguments through authorized history before validation/dispatch. A missing source cannot become an empty argument object or a new model call. |
| Pure and deterministic | `plan_resume` MUST depend only on the entries, their explicit owning session id and the tool declarations, and MUST give the same plan for the same inputs. The engine supplies session identity from the leased log, never a model call id or SDK cache; entries alone do not carry that identity. Tool inputs are immutable declaration snapshots; schema hooks and other extension code run outside the pure planner. Every resumed parent must have been interrupted at its child start, without an intervening superseding input. Missing or contradictory source scope/positions refuse planning. A committed final answer may yield an empty plan only with its matching original assistant source; the executor must still authorize and verify that source before finalizing. |
| Effect versus replay | `effect="read_only"` asserts no external mutation; `effect="effectful"` is the default and writes intent regardless of replay. A read-only tool with `replay="unsafe"` also writes intent to preserve its no-automatic-rerun declaration; only read-only plus replay-safe omits intent. `replay="safe"` asserts convergence only under the same invocation id, including idempotent writers. A new id of such a writer is blocked by an unresolved effect. These are tool-author assertions, not sandbox guarantees. |
| What `replay` guarantees | `safe` is the tool author's assertion that rerunning with the same invocation id converges: the tool is read-only, or its downstream is idempotent by that key. `unsafe`, the default, means tether never reruns the tool automatically. Neither is a guarantee about a downstream system. |
| What tether guarantees | Each committed entry appears once. On a store that declares durable commits and fenced leases (CONTRACT-003 section 3), a tool that is not safe is run at most once automatically, and a replay-safe invocation is eligible for repeated same-id attempts while current authorization, its original digests, readable arguments and configured recovery bounds permit them. This is not an unconditional completion or at-least-once liveness guarantee. When tether cannot tell whether a tool ran, it says `outcome_unknown`. |
| What tether does not guarantee | Exactly-once external effects. That needs support from the downstream system, such as an idempotency key it honors. |
| Reconciliation | `Tool.reconcile` is an optional read-only check through the mediated retrieval/policy path. `Completed` records the guarded result as a clearly labelled non-executable assistant resolution notice plus a resolution entry; it never adds a second tool-result message for an already-answered call; `NotCompleted` records resolution without authorizing another execution; `Unknown` leaves the barrier. A new attempt requires a present actor, fresh gates and its own invocation-bound approval when the tool/approval policy requires one. Reconciliation is never itself a retry. An outstanding same-process worker refuses reconciliation/resolve with `WorkerOutstanding`. |
| Manual recovery | `Client.resolve` is P0 and records `completed` or `not_completed` with a bounded operator reason after `Policy.authorize(RESOLVE_EFFECT)`. It does not execute a tool. Resolution uses the session lease and an invocation-scoped idempotency key, has one winner, and a conflicting duplicate is `ResolutionConflict`. The outcome cannot be guessed from a new model call or approval. |
| Effectful failures | Any abnormal exit after an effectful intent (exception, timeout, cancellation, missing response or failed result append) is `outcome_unknown`, unless a separate authoritative reconciliation proves the outcome. An ordinary exception is not proof of no effect. The result may be recorded as `interrupted`, but the unresolved barrier persists until an explicit resolution. |
| Session-wide barrier | The conservative barrier blocks every effectful tool in that session, not merely a matching name/argument digest. Tether does not infer semantic equivalence across calls or sessions; downstream operation keys, fencing and global business constraints remain application obligations. The model gets a typed denial and can explain/read safely; repeated denials remain subject to the step limit. |
| A failed result append | A result commit that fails after the tool ran leaves the call started with no result, the same state as a crash. |
| A timeout | A timeout after effectful intent is `outcome_unknown`, including a replay-safe writer; so is abandoning a plain function in a worker thread, which cannot be cancelled. |
| Resume chain | After `max_resumes` consecutive interruptions of one chain, `resume` is refused with `ResumeLimit`. |
| Identity | Resume is explicit and needs a present authorized actor and the selected identity mode, with a current credential where required. Automatic resume is opt-in and supplies an explicitly authorized identity (ADR-010); it never resurrects a stored credential. |

### 11. Interaction (in-process drivers)

```python
class Answer(BaseModel):  approved: bool; note: str | None = None
class Interaction(Protocol):                         # for drivers that answer in-process: tests, the CLI, scripts
    async def decide(self, pending: PendingConfirmation) -> Answer: ...

async def drive(client: Client, session_id: str, text: str, interaction: Interaction, **options) -> TurnResult: ...
```

`drive` sends a message through the client API (CONTRACT-003), and while the turn stops for confirmation asks `interaction`, answers, and continues. Dummies: `AutoApprove`, `AutoDecline`, `ScriptedAnswers(list)`, and `ConsoleInteraction`. A server adapter exposes confirmation as its own route and does not use `Interaction`.

### 12. Conformance suites

The package ships `tether.testing.conformance`, as asynchronous tests run with the `anyio` pytest plugin: `policy_suite(factory)` (fail closed, one decision per action, a budget denial from `usage`), `audit_sink_suite`, `tracer_suite`, `provider_suite(factory)` (ordering, output validation, reason normalization, fail closed, registry conflicts, a plain `def` hook does not block the loop), `guard_suite(factory)`, `resume_planner_suite` (every derived state, and the prefix property over every log prefix), and `approval_suite` (binding, single use, concurrent answers, supersession, expiry, gate re-entry). An implementation of a port is conformant when it passes its suite. The model port and store suites are in CONTRACT-001 and CONTRACT-003; history/correlation suites are in CONTRACT-005. The live store, history provider and model context have separate authorities. SDK history/conversation usage/deferred requests, if selected, are versioned derived runner state, not a second transcript or approval/effect authority. Add `context_strategy_suite`, `token_estimator_suite` and `context_executor_suite` (CONTRACT-004), unresolved-effect barriers across new model ids and different tools, actual worker-lifetime capacity, queued cancellation, bounded shutdown, and read-only reconciliation/one-winner resolution. TP-001 allocates these cases.

## Precedence and Compatibility

The initial v1 is revised before release, including CONTRACT-004 and `RESOLVE_EFFECT`; no compatibility layer is required.

- Versioning: one integer, `ENGINE_CONTRACT_VERSION=3`. Version 3 requires storage-scoped `TurnLog.workers: WorkerLifetime` and an explicit shared lifetime for pending inspection. Version 1/2 provider hooks remain supported unchanged. Version 2 adds explicit model principal and the enforced READ_CALL_PAYLOAD action. Existing version-1 providers retain the identical hook surface and are supported; policies must handle the new action or fail closed. A provider declares the version it was written for in `Provider.contract`; the registry refuses a provider above the engine's and accepts a lower one the engine still supports. `AgentSpec.schema_version` versions a specification separately.
- Ordering or precedence: the gate sequence of section 8 is normative and overrides any provider's expectation.
- Backward-compatibility rules: adding an optional method with a default to `Provider`, a new action string the engine does not itself raise, or a new optional field is compatible. Changing a signature, adding a reason the engine reads, reordering the sequence, or tightening a rule is a new version. There is no synchronous form, so asynchrony is not a compatibility axis.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| `ModelUnavailable` | Turn ends `model_unavailable` with the reason and the provider's request id | The user may resend | The log names the failed call |
| `Policy` raises | Denial `policy_unavailable` | No | Restore the policy source |
| `ScopeBinder` or `SpecResolver` raises | Turn ends `scope_required`, generic reply | No | Restore it |
| Provider method raises, or returns an invalid output | Section 4 "Fail closed" and `provider_output_invalid` | No | Fix the provider |
| `ResultGuard` raises | Result withheld, `<id>_unavailable` | No | Restore the guard |
| `EventSink` raises | Event dropped, turn unaffected | No | The consumer reconnects and reads the log |
| Approval digest mismatch | Call denied, `approval_invalid` | Ask again | The person approves what is now requested |
| The tool ran and the result append failed, or the process died | The invocation's outcome is unknown | A present caller resumes; reconcile or resolve | Section 10 |
| A tool times out | Read-only tool: typed `timeout`; effectful tool: `interrupted` with `outcome_unknown` and durable barrier | Read-only or same-invocation recovery only; new effectful calls require resolution | Section 10 |
| Turn cancelled | Steps already committed remain; `turn_ended: cancelled` appended; cancellation propagates | The person may resend | The log is consistent |
| The lease is lost during a turn | The engine stops; its further appends fail with `LeaseLost` | No | Recovery resolves the turn |
| A tool raises | Read-only tool: typed `tool_error`; effectful tool after intent: `outcome_unknown`, barrier remains | Read-only or same-invocation recovery only; no new effectful invocation until resolution | Section 10 |
| Outstanding worker | `WorkerOutstanding`; new turn/resolve refused for that local session; capacity stays occupied until actual exit | After actual worker exit | A late worker result never commits through a stale TurnLog or silently clears an unknown |
| A tool or hook blocks the loop | Not detectable by the engine; a plain `def` is offloaded; an `async def` that blocks is a defect found by the `ASYNC` lint rules and review | n/a | Fix the code |

## Examples

`Engine` additionally receives `context`, `context_config`, and `estimator` under CONTRACT-004. Its executor settings require finite `worker_limit`, `worker_queue_limit`, and `shutdown_timeout_seconds`; values are explicit application settings. Shutdown waits at most that deadline, reports outstanding workers and does not claim their effects stopped. Worker count/capacity remains bounded for the executor lifetime.

```python
# An application composes the ports (the only place adapters are imported):
engine = Engine(
    model=OpenAICompatModel(settings.model),         # ADR-002
    policy=AllowAll(), binder=NoScope(), resolver=FixedSpec(SPEC),
    providers=[NotesProvider()], guards=[],          # the default guard list is empty
    audit=ListAuditSink(), tracer=NullTracer(), clock=SystemClock(), ids=UuidIds(),
)
```

```python
# A tool as a plain function; it declares it is unsafe to rerun (the default) and passes the invocation id downstream:
@tool(always_ask=True, timeout_seconds=30.0)
def send_email(ctx: ToolContext, to: str, body: str) -> str:
    receipt = mail_client.send(to=to, body=body, idempotency_key=ctx.invocation_id)
    return f"sent {receipt.id}"
```

## Non-Normative Notes

- The provider surface is the extension point for features an application layers on the loop (stages, skills, attachments, document access). The first downstream application's features are the test that it is neither too narrow nor too wide.
- Citations and typed results are the application's: a terminal tool whose validated arguments carry the answer and its sources needs no core change (SD-001, boundary scenarios). Artifact bytes live in the application's storage; a tool returns a reference as an attachment.
- Engine-mediated summary calls are the only auxiliary model path in v1 (CONTRACT-004). Arbitrary model calls from tools/providers still bypass the gates and remain an extension-author obligation not to make.
- Running independent tool calls of one reply in parallel is not part of v1. It changes the order of log entries and audit records and is a decision of its own.
- Context selection, compaction and request bounds are in v1 under CONTRACT-004; `before_request` remains a narrowing hook and does not synthesize a summary. No autonomous memory or external retrieval toolkit is added.

## Open Items

| Item | Owner |
|------|-------|
| The time limit for a provider hook, the default tool timeout, and `approval_ttl`, if any | Erik LaBianca |
| Qualified target-specific reconcilers and cross-session business-effect deduplication | Application/tool author; core never infers equivalence |
| Whether `tether.scope` matches the first downstream platform's needs, checked in its parity run | Erik LaBianca |

The session integration may supply a validated `TurnStart` with the journal lease/handle's
turn id, request id, requested spec and immutable attachment refs. Engine computes the canonical
accepted-input digest from text, refs and requested spec. It prepares user content and any
interrupted/superseded call-result content before one commit of closure facts, turn start and
input reference. A trusted synchronous `accepted` notification runs only after that commit;
it carries no dispatch permission. Missing/ambiguous supplied pinned spec ids refuse rather
than selecting a version heuristically. Agent remains the sole model/tool iterator.

`TurnLog.turn_id` carries the lease-bound logical id (None only for volatile test doubles).
New input adopts that id by default and refuses a different supplied id. The acceptance signal
runs immediately after known input commit, before fallible SDK/run setup. A later setup failure
is therefore an accepted-turn failure. Specs and supplied start values are copied before await.


The concrete `tether.types.lifetime.WorkerLifetime` is process-local ownership, never persisted
execution evidence. Every leased log for the same storage namespace MUST expose the same handle;
independent namespaces MUST NOT share handles solely because session ids match. Sync execution and
validation retain actual executor futures through physical completion. Owned async hooks and
returned same-loop awaitables retain an operation completion proxy until actual completion,
including when cancellation is suppressed. Admission precedes execution; cancellation of a
shielded waiter requests child cancellation without cancelling the ownership proxy. Physical
executor capacity and retained-operation capacity are separate, so a tool can make a mediated
nested read without consuming another physical permit for the async parent. The selected
ExecutionLimits defaults are max_workers=8 and max_worker_operations=32; both are positive
application settings. Raw function exit frees its physical permit, while an adopted awaitable
keeps the operation and session busy. Fresh awaitable adoption is bounded to16 levels; abandoned
bare coroutines are closed without starting. Existing returned same-loop Tasks/Futures are
cancel-requested and drained even after a depth refusal. Cycles refuse with a typed error.
Arbitrary detached work and foreign-loop Tasks/Futures are outside this return profile. Foreign-loop
lifetime registrations refuse before registry mutation; returned foreign-loop Futures refuse adoption. The lifetime retains a clean-context loop timer
while active, preventing weak asyncio task cycles from losing the storage owner. Late failures
are consumed without replacing the waiter's primary cancellation. Loop/open checks precede
submission. Invocation intent/result/message entries use their original proposal turn id, including
first dispatch or safe replay during recovery; recovery-attempt facts use the accepting phase id.


### Telemetry delivery and authority

Journal facts remain authoritative. Audit delivery is a bounded observational mirror after known
acceptance, with no durable-delivery or exactly-once promise. Events have stable identity derived
from session id, accepted entry position and semantic phase. Stable-key retries MUST NOT create
new logical events. Unknown or rejected appends MUST NOT be emitted as accepted outcomes. The core
maps semantic transitions to the declared audit kinds; it MUST NOT serialize raw journal facts,
receipts, history refs, exception messages or arbitrary extension dictionaries as audit detail.

Trusted input/confirmation/resume acceptance notification MUST occur before any telemetry await.
Sink/tracer failure MUST NOT authorize an action, reverse a committed result, repeat an effect,
mask the primary exception or erase cancellation. Explicit finite queue/delivery/shutdown bounds
control owned observational tasks; overload/failure is metadata-only and cannot block a turn
indefinitely. External sinks and exporters remain application-owned. A durable outbox is a separate
profile and is not implied by this base contract.

Default tracing/audit perform no I/O. The collecting audit dummy has explicit finite capacity and
reports overflow. Trace/span ids exist only for real trace context; no-op tracing MUST NOT invent
ids. Disable automatic exception recording and exception-derived status text. Engine metadata uses
explicit field/type registries and configured names/prefixes, with owner and current principal
separate and no credentials or unrestricted actor attributes. Tool wait spans MUST NOT claim
physical completion after cancellation; outstanding/exit observations retain captured identifiers.
Per-attempt usage uses CONTRACT-005's shared typed reduction, including current purpose, retries,
missing usage and optional subcounts. OTel exporter/receiver proof remains separately qualified P1.


Telemetry contract version1 uses the interfaces in section6. Tracer/span operations are pure,
nonblocking synchronous context operations; export and sink I/O is asynchronous and external.
Span enter/exit MUST occur in the same task/context. Nested spans preserve the injected tracer's
actual parent across awaits; copied worker context MUST NOT alter the caller's context. A disabled
tracer returns None for ids/traceparent even under unrelated ambient instrumentation. A recording
test tracer requires an explicitly supplied id factory; fabricated test ids prove context structure,
not an OTel ingestion path. Traceparent uses the supplied real trace flags; ids are never turn ids.

AuditContext contains copied identifier values. Trace/span ids are both absent or both valid;
partial, zero and malformed ids refuse construction. Null context, finite collecting capacity and
explicit overflow are conformance cases. The sink receives an owned event snapshot; application
sink code may mutate that packet, but MUST NOT gain mutable journal/actor/credential objects.
Application fixtures expose captured records for reusable audit_sink_suite/tracer_suite checks;
no pytest runtime dependency or default exporter is required. External delivery, ingestion and
shutdown proof remains separate from logical port conformance.

Accepted-event id version1 is the canonical digest of ["tether.audit.v1", owner, session_id,
accepted_entry_position, semantic_phase]. Session creation uses a separate genesis phase and no
journal position. Pure physical worker-exit observations link the captured accepted intent position,
physical worker-attempt id and lifecycle phase; they do not require a journal append after lease
closure and do not assert durable acceptance. A canceled tool wait is never a physical exit.


The test-kit fixture API is explicit and uses only Tether-owned records:

```python
class AuditRecord(Payload):
    context: AuditContext
    kind: str
    detail: dict[str, JsonValue]
class TraceObservation(Payload):
    name: str
    ids: TraceIds
    parent: TraceIds | None
    attributes: dict[str, TraceScalar]
    error_reason: str | None
    finished: bool                        # span context closed, not a physical worker exit
    overflow: int
@dataclass(frozen=True)
class AuditSinkFixture:
    sink: AuditSink
    read: Callable[[], Awaitable[tuple[AuditRecord, ...]]]
    captures: bool
@dataclass(frozen=True)
class TracerFixture:
    tracer: Tracer
    read: Callable[[], Awaitable[tuple[TraceObservation, ...]]]
    captures: bool
```

Factories return fresh async context-managed fixtures. A capturing audit fixture has room for at
least two events; a capturing tracer fixture has room for six completed spans. The application
owns fixture setup, receiver flushing and resource cleanup. Both suites accept a finite positive
`timeout_seconds` (default10) and return SuiteReport with captures capability and required external
proof. Captured values retained beyond emit and exposed by read MUST be snapshots; caller reuse
or mutation MUST NOT change earlier retained records. Logical conformance is not receiver proof.

ListAuditSink requires explicit positive capacity and uses max_record_bytes (default16384) over
canonical encoded records. Capacity drops increment overflow; byte-limit drops increment oversized.
Both retain no dropped packet. RecordingTracer requires an explicit id factory and positive capacity;
max_attributes (default32) and max_value_bytes (default1024) bound retained names/values. Attribute
or span-capacity drops increment their corresponding overflow counters. Invalid/nonfinite metadata,
invalid reason codes, changed child trace identity, and updates to a closed recording span refuse
with bounded static codes. Context-manager failure records no automatic exception/status text.


Engine observer delivery uses explicit finite max_pending_events, max_event_bytes,
dedup_capacity, delivery_timeout_seconds and shutdown_timeout_seconds. Default sink omission
retains no packets and starts no delivery task. A non-None supplied port is honored even if falsey.
Pending admission includes queued packets and scheduled cross-thread callbacks; one separate
physical delivery permit remains occupied until the sink coroutine actually exits. After timeout,
cancel the wait and retain the surviving emission; do not spawn replacement deliveries around it.
Delivery and shutdown failures/drops are bounded counters, never exception text or raw packets.

Create dispatcher and emission tasks in clean context-variable contexts. Packets hold copied scalar
owner/principal/session/turn/attempt/spec/trace values only, never Actor, Credential or span-manager
objects. The explicit captured packet trace context owns correlation; the dispatcher's ambient span
MUST NOT be attached to another actor's event. The bounded admission/dedup/build path before a
trusted accepted callback MUST NOT throw or await. Equal retries reuse observational identity;
bounded deduplication is not a durable exactly-once guarantee after eviction or restart.

Use one absolute caller shutdown deadline across observer and physical cleanup stages. Seal
observation admission, retain surviving observer tasks, and expose incomplete delivery without
holding a session lease, file lock, database acquisition or store root solely for observational
work. Observer cleanup failure MUST NOT skip physical-worker or storage cleanup. Actual execution
workers still retain their existing ownership until exit. Engine/service cleanup distinguishes
physical completion, store release and observer drain rather than making a stalled sink their
shared authority.

Executor-function exit and completion of an awaitable it returns are distinct observations. Neither
alone claims accepted tool-result commitment. Span enter/exit stay in the owning thread/task;
late observations use captured intent position and physical-attempt identity. Guard tracer entry,
metadata access, mutation and exit so failure/suppression cannot mask the original execution error
or cancellation. Do not pass the body's exception object to a tracer for automatic recording.


TelemetryLimits defaults are max_pending_events=256, max_event_bytes=16384,
dedup_capacity=1024, delivery_timeout_seconds=1 and shutdown_timeout_seconds=1. All limits
are strictly positive; byte limits count canonical encoded bytes. DeliveryStats exposes pending
(queued plus scheduled), active, sealed, disabled and nonnegative counters enqueued, delivered,
failed, timed_out, dropped_full, dropped_bytes, dropped_closed and duplicates. Observational
counters do not imply journal durability, exporter receipt or successful application effects.
After bounded or canceled close, surviving delivery MUST retain a strong owner through actual
coroutine exit even when the caller drops its last reference. Release that owner after drain.


Engine accepts optional audit:AuditSink, tracer:Tracer and telemetry_limits:TelemetryLimits.
Explicit None selects the no-op; falsey supplied ports remain active. audit_stats returns a copied
DeliveryStats, and observation_failures counts
metadata-build/context and guarded-tracer failures separately from delivery counters.
Engine.aclose shares one absolute finite deadline across execution and observer waits. Composition
may call aclose_execution(deadline=...), wait_closed() for actual execution only, release storage,
then aclose_observers(deadline=...) and wait_observers_closed(). A False close is incomplete;
observer drain MUST NOT be a prerequisite for releasing otherwise drained session storage.
SessionService uses this physical/store/observer ordering with one caller deadline. It seals observer
admission after physical/store cleanup; late observations after that frontier drop explicitly.

The accepted-entry mapping is a closed registry. Unknown extensions and fields are omitted, and
known-invalid/unregistered codes map to other. No history hydration is required for telemetry.
Common detail contains entry_position, source_turn_id and phase_turn_id; the last names the
accepting lease phase, even when an entry retains an earlier proposal's source turn. Identity fields
are copied metadata; tool/provider call payloads, receipt identifiers and history refs never pass.

| Accepted source | Audit kind | Additional allowed detail |
|---|---|---|
| message with user/assistant role | user_message / assistant_message | None |
| authorization | authorization | Closed action, strict allowed boolean, closed reason, resource_digest; actual purpose for model gates |
| model_attempt / model_receipt | x.model.prepared / x.model.observed | Generated call/attempt IDs, actual purpose, prepared status when present, known per-attempt usage and usage_complete |
| model_call | model_call | Generated call ID, actual purpose, closed status/finish_reason/reason, shared per-call retry usage and usage_complete |
| tool_call | tool_call | Bounded tool/invocation IDs and binding digests, closed status/effect/replay/reason |
| confirmation | confirmation | Bounded invocation ID and closed state; no notes/arguments |
| turn_started / turn_resumed / turn_ended | x.tether.turn_started / turn_resumed / turn_ended | Closed stop_reason/reason for ending |
| session with closed event | session_closed | None |
| x.effects.resolved / x.effects.replayed | Same namespaced kind | Bounded invocation/tool/binding identifiers only; no operator text |

Per-call usage uses an incremental index over the shared accepted-fact ledger, including retries,
known zero, optional subcount enrichment and missingness. A later absent receipt does not erase
known counts. A compatibility test recorder can provide known logical final counts while physical
coverage remains incomplete. Model-call spans close after response validation and known response
acceptance, so they observe that same ledger. The trusted runtime observation callback yields only
traceparent; it cannot replace model identity, requests, executable results, approvals or dispatch.
Gate/model audit context is captured at the corresponding operation, not from dispatcher context.
Bounded source-context caches use max_pending_events and retain copied IDs/weak source refs only.

This Engine accepted-journal checkpoint does not qualify nonjournal Client read/genesis/metadata
operation auditing, physical executor lifecycle, tool/retrieval/context spans, or real OTel transport.
Those remain required integration work in the build plan; reads MUST NOT append fictitious facts or
manufacture journal positions to fill their observation gap.


Tool dispatch observations capture an immutable scalar descriptor after known required intent
acceptance: owner/current principal/session, source turn/index and call position, accepting phase,
invocation, optional exact accepted intent position, spec/version and a new dispatch-attempt UUID.
Replay retains its original intent position; safe read-only dispatch has no invented intent.
Internal Governance.fact returns the exact accepted Entry after validation, preserving ignored-return
callers. An unknown or mismatched required intent cannot construct a dispatch observation or run a tool.

The tool waiter span is tether.tool_call. Actual initial body spans are tether.executor_function
inside the worker thread and tether.async_body inside the executing Task. A queued cancellation or
Task cancelled before body entry emits no body entered/exited claim. Returned awaitable adoption
remains separately owned; raw/body exit does not establish operation completion or accepted success.
Lifecycle kinds x.executor.entered/exited/outstanding/completed/refused use fixed scalar fields and
closed outcomes returned/raised/cancelled. The descriptor and callbacks contain no Actor, credential,
arguments, output, exception object/text or span manager. Body trace IDs are copied values only.
Each observational event ID digests ["tether.worker.v1", owner, session_id, source_position,
worker_attempt_id, phase]; separate dispatches of one invocation have distinct identities.

Outstanding reports the completion proxy observed live after waiter cancellation; it is neither a
durable status nor a claim that the body stopped. Nearby outstanding and exited events are valid
observations and delivery order across threads confers no execution authority. Completion hooks run
in clean contexts, classify terminal state without exposing its content, and never commit a late
result. Existing bounded delivery owns admission/drop counters. Observer sealing can drop late
metadata explicitly; observers cannot retain storage after actual execution drains. No new public
port, exporter or Agent runner is introduced by these internal hooks.


Client nonjournal authorization observations use kind authorization, origin="client", closed
action/reason, strict allowed boolean and resource_digest. Identity is the digest of
["tether.client.gate.v1", fresh gate-request UUID]; detail includes gate_request_id. Context carries
current copied owner/principal and an explicitly supplied session id only; no journal position,
source turn, lease or inferred session identity is added. Tracing uses tether.gate; metadata commands have tether.client.create/update parent spans. Canceled
authorization propagates and produces no fabricated completed decision. Policy exceptions retain
fail-closed behavior and normalize observational reason to policy_unavailable. Namespaced archive
actions already used by the Client are registered metadata codes, not new policy actions.

Receipt-backed Client genesis is session_started with identity digest
["tether.audit.v1", "genesis", owner, session_id], commit_seq and origin="client", with no entry
position. Only a validated created receipt emits it; existing returns emit no genesis. Each accepted
metadata Entry emits session_settings with the existing accepted-event ID formula, exact
entry_position/source_turn_id/commit_seq and category title|tags|settings. No values, custom
setting names or raw receipts are audit detail. Noop emits no semantic metadata mutation.

Client receipt acquisition is bounded separately by ClientLimits.max_mutation_receipt_bytes
(default4MiB, positive). Fresh-dictionary validation/copy verifies version, shape, scope, exact
frontier and requested identity before semantic admission. Invalid/over-budget/unknown receipts
emit no accepted semantic event and never trigger a legacy write retry. A valid known mutation is
admitted synchronously before later authorization and public summary-size/disclosure checks.
Thus a later disclosure failure cannot erase known acceptance; bounded delivery remains lossy.

For capability0 adapters, x.client.create_returned and x.client.metadata_returned are emitted only
after the entire authorized return succeeds. They carry a fresh observational ID, origin="client"
and semantic_receipts=false, without a commit sequence, entry position or genesis claim. They do
not establish full semantic mutation-audit qualification. With audit disabled, legacy command paths
are preserved. The fixed Client observer shares Engine delivery/cleanup; no new authority port,
application callback, writer or runner is introduced.


Mediated retrieval observations describe the waiter after a known accepted allowed RETRIEVE
decision. RetrievalDenied is an ExecutionDenied subtype raised only for a known committed policy
denial; store/lease/unknown/mismatched-acceptance failures keep their error identity. An error
raised by fetch is not converted into RetrievalDenied. Denied or unaccepted authorization never
opens a retrieval span or starts fetch.

The tether.retrieval span and nonjournal retrieval audit record carry origin="waiter", a fresh
retrieval_attempt_id, invocation_id, original source_position/source_turn_id, current phase_turn_id,
authorization_position and resource_digest. authorization_position is the exact accepted policy
Entry index. It is not a retrieval-result position; no entry_position is invented. Audit context
carries current owner/principal/session/spec IDs and copied span IDs. Record identity is digest
["tether.step.v1", owner, session_id, "retrieval", retrieval_attempt_id]. Outcome is the closed
returned|raised|cancelled wait outcome. The engine does not inspect, size, serialize or log fetched values or exception content merely
for telemetry. It hashes the resource for correlation without retaining or emitting its plaintext. Cancellation says only
that the wait was canceled; actual fetch lifetime remains owned by the existing worker Operation.
There is no retrieval success journal fact, effect proof or additional execution authority here.

Selected provider-hook observations use tether.provider_hook and x.<provider id>.hook, with a
fresh hook_attempt_id, origin="waiter", current phase_turn_id, pinned provider_id, method and the
same closed outcome set. Identity is digest["tether.step.v1", owner, session_id, kind, hook_attempt_id].
Methods are capabilities|prompt_section|hidden_tools|before_request|before_call|after_call. They
observe the existing bounded hook wait, including adopted returned awaitables. A timeout is raised;
caller cancellation remains cancelled. Returned means the wait returned, before subsequent output
validation or narrowing acceptance; it does not authorize a model/tool call or approve a hook result.

The observer captures provider namespaces at Engine construction only when telemetry is selected.
A namespace must match [a-z][a-z0-9_]{0,63}; an invalid, unregistered or changed namespace at wait
entry, or an unknown method, omits that observation and increments a secondary observer failure counter. Omission does
not change existing provider execution or claim to qualify its configuration. No runtime str/repr
conversion can register a namespace. These fixed observations do not forward Provider dictionaries,
ToolOutput.facts, Actor.attributes, capabilities, prompts, messages, output values or arbitrary
x.* journal facts. Unknown journal extension facts remain omitted by the accepted-fact registry.
Application fetch implementations and any later configurable third-party scalar schema remain
outside this checkpoint. With both audit and tracing omitted, no step UUID/digest/packet is built.
