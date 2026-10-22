---
ddx:
  id: CONTRACT-002
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-22
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
    - id: architecture
      kind: informed_by
---

# CONTRACT-002: Engine, Tools, Providers, and Governance Ports

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-002
**Type**: library (Python ports)
**Version**: v1
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
    credential: Credential | None = Field(default=None, exclude=True, repr=False)   # CONTRACT-001; never stored (ADR-011)

# Actions are strings. The core defines these; an optional package defines its own as `x.<package>.<action>`.
CALL_MODEL = "call_model"; CALL_TOOL = "call_tool"; RETRIEVE = "retrieve"
APPROVE = "approve"; RESUME = "resume"; READ_SESSION = "read_session"

class Decision(BaseModel):  allowed: bool; reason: str                          # "allowed" when allowed
class AgentSpec(BaseModel):                                                      # what the loop reads of a task
    schema_version: int = 1
    id: str; version: str; title: str; instructions: str; model: str            # model is a tier
    tools: tuple[str, ...] = (); max_steps: int = 20
    ext: dict[str, JsonValue] = {}                                               # blocks the engine passes untouched to providers, by namespace
class PendingConfirmation(BaseModel):
    invocation_id: str; tool: str; args_digest: str; tool_digest: str
    requested_at: datetime; expires_at: datetime | None = None
StopReason = Literal["final", "step_limit", "model_denied", "model_unavailable",
                     "scope_required", "confirmation_required", "cancelled", "interrupted"]
class TurnResult(BaseModel):
    turn_id: str; stop_reason: StopReason; reason: str | None = None            # the outage's short cause
    messages: tuple[Message, ...] = ()

class Engine:                                                                    # constructed with the ports; no environment reads
    async def run_turn(self, actor: Actor, log: TurnLog, text: str, *, spec: AgentSpec | None = None,
                       attachments: tuple[AttachmentRef, ...] = (), events: EventSink | None = None,
                       parent: object | None = None) -> TurnResult: ...
    async def answer(self, actor: Actor, log: TurnLog, invocation_id: str, answer: Answer, *,
                     events: EventSink | None = None, parent: object | None = None) -> TurnResult | None: ...
    async def resume(self, actor: Actor, log: TurnLog, *, events: EventSink | None = None,
                     parent: object | None = None) -> TurnResult: ...
```

`TurnLog` is the live store's write handle for one turn (CONTRACT-003). `Engine.answer` returns `None` when `invocation_id` is not the pending approval or another answer already won, and changes nothing. The engine reads `Decision.reason` for the values `allowed`, `tool_not_permitted`, `scope_required`, `approval_invalid`, `budget_exceeded`, and `policy_unavailable`; every other reason is recorded verbatim and treated as a denial. A provider's reason MUST be `x.<provider id>.<reason>`.

The engine owns the loop, tool registration, and tool resolution by name. Finding tools or workflows for the model, engagement and routing, the specification a session is pinned to, stages, skills, and suggestions are the application's or a provider's, and read what they need from `AgentSpec.ext`. The `turn_started` entry records the specification's id, version, and digest, and a resolver may pin it (ADR-017, ADR-018).

### 2. Policy, scope binding, and spec resolution

```python
class UsageSoFar(BaseModel):  steps: int; input_tokens: int; output_tokens: int; elapsed_seconds: float
class AuthzRequest(BaseModel):
    actor: Actor; action: str; resource: str
    held: dict[str, tuple[str, ...]] = {}            # the session's scope values, an opaque map to the core
    requires: frozenset[str] = frozenset()           # keys the call needs held, an opaque set to the core
    permitted: frozenset[str] | None = None          # names the caller may use, when the action has a list
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
| Budgets | A policy MAY deny `CALL_MODEL` with `budget_exceeded` from `usage`. The core enforces only `AgentSpec.max_steps`. Token, wall-time, and cost thresholds are the policy's and their values the application's. |
| Resolution | The engine calls `SpecResolver.resolve` once per turn, before binding, with the spec the caller named or `None`. `NeedsInput` ends the turn like a binder's. A resolver that raises ends the turn with a generic reply and no model call. |
| Binding | The engine calls `ScopeBinder.bind` once per turn before any model call or tool call, records its updates in a `settings` entry, and rebuilds `held` from the log. `NeedsInput` ends the turn with stop reason `scope_required` and the reply as an assistant message; no model call is made. A binder that raises ends the turn the same way with a generic reply. |

### 3. `SessionState`, what the engine reads of a session

```python
class SessionState(BaseModel):                       # loaded by the store when the turn lease is taken
    id: str; owner: str; messages: tuple[Message, ...]
    held: dict[str, list[str]] = {}                  # rebuilt from the log; opaque to the core
    model: str = "auto"; effort: str = "auto"; approval: Literal["auto", "manual"] = "auto"
    disabled_tools: tuple[str, ...] = (); pending: PendingConfirmation | None = None
```

The state is a snapshot of the log at the lease (CONTRACT-003). The engine changes a session only by appending entries through the `TurnLog`; it never mutates `SessionState` in place and never persists it as a document. An application's own data is not in this type (ADR-018); per-turn application data travels in `Actor.attributes` or a capability.

### 4. Tools and the provider surface

```python
class ToolOutput(BaseModel):  text: str; attachments: tuple[AttachmentRef, ...] = (); facts: dict[str, JsonValue] = {}

class ToolContext(Protocol):
    scope: dict[str, tuple[str, ...]]; actor: Actor; invocation_id: str
    def capability(self, kind: type[T]) -> T: ...    # raises CapabilityUnavailable
    async def read(self, resource: str, fetch: Callable[[], T | Awaitable[T]], *, value: str | None = None) -> T: ...

class Tool(Protocol):
    name: str; description: str; input_model: type[BaseModel]
    requires: frozenset[str]                         # scope keys the call needs held; opaque to the core
    model_visible: bool; always_ask: bool
    replay: Literal["safe", "unsafe"]                # default "unsafe"; the author's declaration (section 10)
    timeout_seconds: float | None                    # enforced by the engine; None means no limit set by the tool
    traits: dict[str, JsonValue]                     # declarative metadata guards may read, such as {"outside_content": true}
    definition_hash: str | None                      # set for a connected tool; a changed hash refuses the call
    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool: ...                       # synchronous: no I/O
    def run(self, ctx: ToolContext, args: BaseModel) -> str | ToolOutput | Awaitable[str | ToolOutput]: ...   # def or async def
    async def reconcile(self, ctx: ToolContext, args: BaseModel, invocation_id: str) -> Reconciled: ...   # optional (P1)

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

A function tool: the library also offers a decorator that builds a `Tool` from a typed function, deriving `input_model` and the JSON schema from its signature; `@tool` is sugar over this protocol and adds no rules. A tool that returns a `str` is the same as one that returns `ToolOutput(text=...)`.

| Rule | Requirement |
|------|-------------|
| Plain functions | A tool's `run` or a provider method written as a plain `def` MUST be run by the engine in a worker thread, with the caller's context variables copied and a bounded concurrency limiter, so it cannot block the event loop. A function written as `async def` runs on the loop and MUST NOT block. |
| Mediated reads | A tool SHOULD read data through `ToolContext.read`. The engine authorizes `RETRIEVE` for `resource`, records the decision, opens the retrieval span, then calls `fetch` (awaiting it if it is awaitable); a denial raises `RetrievalDenied(reason)` to the tool. The engine guarantees this for reads that go through `read`; it cannot guarantee that a tool uses it (section 7). |
| Capabilities | `ToolContext.capability` returns what a provider put on the context for this call. A kind no provider supplied raises `CapabilityUnavailable`, which the engine reports to the model as a typed tool error. A second provider MUST NOT add a field to the engine's types. |
| Order | Providers run in registration order. |
| `before_request` | Hooks chain: each receives the previous hook's output. The result MUST be a tuple of messages that is a structural subset of the input: the same or fewer messages, unchanged roles, no added tool calls, and attachments that are a subset. An output that is not is a `provider_output_invalid` failure of the step. Whether a change to a message's content is a faithful redaction is the author's obligation. |
| `before_call` | Every provider's hook runs in order. It returns a `Refusal` or `None`; `None` means no objection and never an allow. The first refusal wins and is recorded with its provider id. |
| `after_call` | Hooks chain. The returned message MUST keep `role`, `tool_call_id`, and `is_error`; a change to any of them is `provider_output_invalid` and the result is withheld. If the final content differs from what the guards saw, the engine MUST re-run every guard on the final content. |
| Reasons | A reason without the `x.<provider id>.` prefix is normalized to `x.<provider id>.invalid_reason`. |
| `prompt_section` and `hidden_tools` | `prompt_section` adds text to the instructions; it is not an authorization and the contract does not treat it as one. `hidden_tools` removes tools from what the model is offered. |
| Time limit | A hook that exceeds the engine's configured time limit fails closed. The value is the application's. |
| Fail closed | A provider method that raises: `before_call` is a refusal with reason `x.<id>.provider_error`; `after_call` replaces the content with a withheld notice; `before_request`, `prompt_section`, `hidden_tools`, and `capabilities` end the step as `provider_error`. The exception is logged by class name only. |
| Registry | Provider ids are unique; tool names are unique across providers; a clash is refused at load with `provider_conflict`. |
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
class AuditContext(BaseModel):  actor: str; session_id: str | None; trace_id: str | None; span_id: str | None; spec_id: str | None; spec_version: str | None
class AuditSink(Protocol):
    async def emit(self, *, context: AuditContext, kind: str, detail: dict[str, JsonValue]) -> None: ...
class Tracer(Protocol): ...                          # synchronous: start_as_current_span, current_ids, set_current_attribute
```

| Rule | Requirement |
|------|-------------|
| Audit kinds | The closed set `session_started`, `user_message`, `assistant_message`, `model_call`, `tool_call`, `authorization`, `confirmation`, `retrieval`, `turn_ended`, `turn_cancelled`, `turn_interrupted`, `turn_resumed`, `session_settings`, `session_closed`, plus `x.<provider id>.<kind>`. One entry per step and one per gate decision. |
| Spans | `tether.turn` (root), `tether.model_call`, `tether.tool_call`, `tether.gate`, and `tether.retrieval`, with attributes in the `tether.` namespace carrying identifiers, sizes, tiers, token counts, and reasons. The prefix MAY be configured; a deployment keeps it stable. Spans carry across awaits through context variables. |
| Content | Spans and audit entries MUST NOT carry message or tool content, argument values of a denied or invalid call, or a credential. Content capture is a separate, opt-in provider and is off by default. |
| Usage | A model call's token usage is recorded on the span, the audit entry, and the session log (CONTRACT-003). |
| Written by the engine | Only the engine writes audit entries and spans for engine decisions; an extension's own audit entries use its namespace. |
| Defaults | `NullTracer` and `ListAuditSink` ship; an OpenTelemetry adapter ships as an extra. |

### 7. The trust model

Python tools and providers are trusted application code (ADR-016). They run in the application's process with its privileges. Tether is not a sandbox.

| Enforced by the engine | Obligations of the extension author (not enforced) |
|------------------------|----------------------------------------------------|
| The gate sequence runs, in order, on every call | Read data through `ToolContext.read`; do no independent network, file, or process I/O the application did not intend |
| A provider's return value has the type and shape of section 4, or the step fails closed | Redact faithfully in `before_request`; do not disclose `ctx.actor.credential` |
| A hook, guard, or policy that raises is a refusal | Declare `replay`, `requires`, and `traits` honestly |
| Policy, approval, and the guards the application configured run on every call | Do not block the event loop; bound runtime |
| Reads through `read` are authorized and recorded | Make downstream calls idempotent with the invocation id where possible (ADR-014) |
| Audit entries and spans are written by the engine and carry no content | |
| A credential is written to no entry, span, audit record, or cassette | |

Isolating code the application does not trust is outside the core: run it in a subprocess or a sandbox behind a tool, or behind the P2 MCP adapter.

### 8. The gate sequence and its commit points

The engine runs these steps in this order. A provider relies on the order and cannot change it. Every `await` is a cancellation point. A step's entries are appended in one `TurnLog.append` call, so each step is committed before the next starts.

**Turn start**: (1) Append `turn_started` and the user message (key `request:<id>` when the caller gave a request id). (2) `SpecResolver.resolve`. (3) `ScopeBinder.bind`; `NeedsInput` appends the reply and ends the turn `scope_required`.

**A model step**: (1) `Policy.authorize(CALL_MODEL)` with `usage`; deny ends the turn `model_denied`. (2) Provider `prompt_section`, `hidden_tools`, and `before_request`, composed per section 4. (3) Call the model port with the actor's credential in its own span; an outage ends the turn `model_unavailable`. (4) Append the reply message and the `model_call` fact (with the scope held at the call).

**A tool call**: (1) `Policy.authorize(CALL_TOOL)` with `requires`, `permitted`, and `usage`; deny returns a typed denial. (2) A connected tool's `definition_hash` MUST equal the pinned hash, else `definition_changed`. (3) Every provider's `before_call`; the first refusal wins and is recorded as a denial. (4) Validate arguments against `input_model`. (5) Approval: if approval is manual, or the tool is `always_ask` and `asks_first` returns true, then if no valid approval exists for this invocation, append the `confirmation` request and end the turn `confirmation_required`; if an answer exists, verify it against section 9, else the call is denied `approval_invalid`. (6) For a tool that is not declared safe to rerun, append `tool_call` with status `started` (key `intent:<invocation_id>`); if the append fails or raises `LeaseLost`, the tool does not run. (7) Run the tool in its own span, under its timeout; failures are typed results. (8) Run the guards in order. (9) Provider `after_call`, chained and validated, re-guarding changed content. (10) Append the tool-result message and the `tool_call` fact with its final status (key `result:<invocation_id>`).

**After an approval answer**: the engine re-enters the tool-call sequence at step (1). The approval satisfies only step (5).

**Resume** (section 10): (1) `Policy.authorize(RESUME)`. (2) Append `turn_started` naming the interrupted turn in `resumes`, and the audit entry `turn_resumed`. (3) For each call by its derived state: run a not-started call through the full tool-call sequence; for a started call that is safe, run the sequence again with the same invocation id (the existing approval is used if its digests still match); for a started call that is not safe, append the `interrupted` result with reason `outcome_unknown` and do not run it. (4) Continue with the next model step.

**Turn end**: append `turn_ended` with the stop reason. A cancelled turn appends `turn_ended` with `cancelled` in a shielded cleanup; recovery appends `interrupted` for a turn whose lease expired.

### 9. The approval lifecycle

| Rule | Requirement |
|------|-------------|
| Binding | An approval binds to the session, the `invocation_id`, the `args_digest` (a hash of the canonical JSON of the arguments shown), the `tool_digest` (a hash of the tool's name, input schema, replay declaration, and always-ask flag, computed for every tool), and the approving actor's id. The credential is never recorded. |
| Single use | One approval covers one invocation. A safe tool rerun on resume uses the same approval; a tool that is not safe is not rerun. |
| Who may approve | `Policy.authorize(APPROVE)` is decided on the answering actor; the default policy lets the session's owner approve. |
| States | `requested`, `approved`, `declined`, `superseded`, `expired`, `invalid`, each a `confirmation` entry. |
| Decline | A decline becomes a denied tool result with reason `declined` and an optional note; the turn continues. |
| Supersession | A new message sent while an approval is pending records each pending call as denied with reason `superseded`, then starts the new turn. A later answer to the old invocation returns `None`. |
| Expiry | If the application sets `approval_ttl`, an answer arriving after `expires_at` (read from the clock port) appends `expired` and nothing runs. No default is set. |
| Concurrent answers | The answer is appended under the key `approval:<invocation_id>`. The store keeps one; the loser gets `None`. Both attempts are audited. |
| Changed permissions | After an answer the sequence re-enters at step (1), so a policy that now denies `CALL_TOOL` denies the call. |
| Changed tool | A digest that no longer matches ends the call as denied with reason `approval_invalid`. |
| Across a restart | The pending state is in the log, and a waiting session holds no lease. The answerer presents a live actor; no credential is stored. |
| After a crash | A crash between the approval and the intent is resumed without asking again, provided the digests and every gate still pass. |

### 10. Execution states and the resume planner

```python
class ResumeStep(BaseModel):  kind: Literal["run", "rerun", "record_interrupted", "model_step"]; call: ToolCall | None = None; invocation_id: str | None = None
class ResumePlan(BaseModel):  interrupted_turn_id: str; steps: tuple[ResumeStep, ...]

def plan_resume(entries: Sequence[Entry], tools: Mapping[str, Tool]) -> ResumePlan: ...   # pure: reads only its arguments
```

| State | The log shows | Resume rule |
|-------|---------------|-------------|
| Not started | The call is in a committed assistant entry; no intent for a tool that is not safe; no result | Run it fresh through every gate. It is not interrupted. |
| Started | A `tool_call` entry with status `started`; no result | A safe tool is rerun with the same `invocation_id`. Any other tool, and a tool no longer registered, gets a result with status `interrupted` and reason `outcome_unknown`, and is not rerun automatically. |
| Finished | A result entry | Nothing. |
| Resolved | A `resolved` entry from reconciliation or a person | Nothing. |

| Rule | Requirement |
|------|-------------|
| Pure and deterministic | `plan_resume` MUST depend only on the entries and the tool declarations, and MUST give the same plan for the same inputs. |
| What `replay` guarantees | `safe` is the tool author's assertion that rerunning with the same invocation id converges: the tool is read-only, or its downstream is idempotent by that key. `unsafe`, the default, means tether never reruns the tool automatically. Neither is a guarantee about a downstream system. |
| What tether guarantees | Each committed entry appears once. On a store that declares durable commits and fenced leases (CONTRACT-003 section 3), a tool that is not safe is run at most once automatically, and a safe tool at least once. When tether cannot tell whether a tool ran, it says `outcome_unknown`. |
| What tether does not guarantee | Exactly-once external effects. That needs support from the downstream system, such as an idempotency key it honors. |
| Reconciliation | An optional `Tool.reconcile` returns `Completed` (the result is recorded as `ok` with a `resolved` entry and the tool is not rerun), `NotCompleted` (the call is run through the gates as a not-started call), or `Unknown` (the interrupted result is recorded). P1. |
| Manual recovery | `Client.resolve` records the outcome of a started call by a present, authorized caller (CONTRACT-003). P1. |
| A failed result append | A result commit that fails after the tool ran leaves the call started with no result, the same state as a crash. |
| A timeout | A timeout on a tool that is not safe is `outcome_unknown`; so is abandoning a plain function in a worker thread, which cannot be cancelled. |
| Resume chain | After `max_resumes` consecutive interruptions of one chain, `resume` is refused with `ResumeLimit`. |
| Identity | Resume is explicit and needs a present actor with a live credential, unless the application opts in to automatic resume by supplying an identity (ADR-010). |

### 11. Interaction (in-process drivers)

```python
class Answer(BaseModel):  approved: bool; note: str | None = None
class Interaction(Protocol):                         # for drivers that answer in-process: tests, the CLI, scripts
    async def decide(self, pending: PendingConfirmation) -> Answer: ...

async def drive(client: Client, session_id: str, text: str, interaction: Interaction, **options) -> TurnResult: ...
```

`drive` sends a message through the client API (CONTRACT-003), and while the turn stops for confirmation asks `interaction`, answers, and continues. Dummies: `AutoApprove`, `AutoDecline`, `ScriptedAnswers(list)`, and `ConsoleInteraction`. A server adapter exposes confirmation as its own route and does not use `Interaction`.

### 12. Conformance suites

The package ships `tether.testing.conformance`, as asynchronous tests run with the `anyio` pytest plugin: `policy_suite(factory)` (fail closed, one decision per action, a budget denial from `usage`), `audit_sink_suite`, `tracer_suite`, `provider_suite(factory)` (ordering, output validation, reason normalization, fail closed, registry conflicts, a plain `def` hook does not block the loop), `guard_suite(factory)`, `resume_planner_suite` (every derived state, and the prefix property over every log prefix), and `approval_suite` (binding, single use, concurrent answers, supersession, expiry, gate re-entry). An implementation of a port is conformant when it passes its suite. The model port and store suites are in CONTRACT-001 and CONTRACT-003.

## Precedence and Compatibility

- Versioning: one integer, `ENGINE_CONTRACT_VERSION`. A provider declares the version it was written for in `Provider.contract`; the registry refuses a provider above the engine's and accepts a lower one the engine still supports. `AgentSpec.schema_version` versions a specification separately.
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
| A tool times out | A result `error` with reason `timeout`; for a tool that is not safe, `outcome_unknown` | The model may retry | Section 10 |
| Turn cancelled | Steps already committed remain; `turn_ended: cancelled` appended; cancellation propagates | The person may resend | The log is consistent |
| The lease is lost during a turn | The engine stops; its further appends fail with `LeaseLost` | No | Recovery resolves the turn |
| A tool raises | Typed `tool_error` result to the model; audit entry; turn continues | The model may retry | None |
| A tool or hook blocks the loop | Not detectable by the engine; a plain `def` is offloaded; an `async def` that blocks is a defect found by the `ASYNC` lint rules and review | n/a | Fix the code |

## Examples

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
- A tool that calls a model itself would bypass the `CALL_MODEL` gate and its audit. Version 1 has no nested-model capability; until it does, calling a model from a tool is an extension-author obligation not to do so outside the port.
- Running independent tool calls of one reply in parallel is not part of v1. It changes the order of log entries and audit records and is a decision of its own.
- Context management for long sessions (compaction) is out of v1; `before_request` is the hook an application can use today to bound the messages it sends, and a `compaction` entry kind is reserved (CONTRACT-003).

## Open Items

| Item | Owner |
|------|-------|
| The time limit for a provider hook, the default tool timeout, and `approval_ttl`, if any | Erik LaBianca |
| `Tool.reconcile` and `Client.resolve` scheduling (P1) | Erik LaBianca |
| Whether `tether.scope` matches the first downstream platform's needs, checked in its parity run | Erik LaBianca |
