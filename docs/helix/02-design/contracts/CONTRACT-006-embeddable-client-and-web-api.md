---
ddx:
  id: CONTRACT-006
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: FEAT-013
      kind: informed_by
    - id: FEAT-014
      kind: informed_by
    - id: FEAT-015
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: architecture
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: TP-002
      kind: informs
---

# CONTRACT-006: Embeddable Client and Web API

> Examples use fabricated data. No credentials, customer values, or private connection details appear here.

**Contract ID**: CONTRACT-006  
**Type**: library, HTTP API, event, and schema  
**Version**: v1 proposed  
**Related**: FEAT-013/014/015, SD-002, ADR-004, CONTRACT-003/005

## Purpose

Define the proposed public Python client and host lifecycle, plus the optional HTTP and browser-facing wire contracts. `tether.client` is the semantic authority for local and remote consumers. HTTP translates that contract without accessing Tether internals.

## Scope and boundaries

- In scope: typed client protocol, host construction settings/lifecycle, supported integration seams, API factory/authentication injection, versioned REST and event surfaces, browser package boundaries, capability negotiation, errors, idempotency, and compatibility.
- Out of scope: application identity implementation, deployment, storage selection, business policy, model providers, UI design tokens, and production deployment qualification.
- Owners: `tether.client` owns Python semantics; `tether_web_api` owns FastAPI, OpenAPI and wire conversion; `tether_web_ui` owns mounted assets and hosting configuration; `@tether/client`, `@tether/react`, and `@tether/ui` own browser packages.
- Normative RFC words MUST, MUST NOT, SHOULD and MAY express requirements. Core Python values use the exact definitions in CONTRACT-003/005 unless extended below.

## Normative surface

### 1. Host construction and ownership

Ordinary construction uses public settings and ports; the caller does not import or instantiate `Engine`. Settings contain explicit model, history, storage, policy, spec, tool, and bound values. Factory calls perform no environment discovery or implicit credential selection. `open()` acquires only resources selected by its explicit public profile; importing the package performs no I/O.

```python
@dataclass(frozen=True)
class LocalJsonlSettings:
    root: Path

@dataclass(frozen=True)
class VolatileSettings:
    pass

@dataclass(frozen=True)
class ProvidedStorage:
    store: SessionStore
    history: HistoryProvider

@dataclass(frozen=True)
class ClientSettings:
    model: ModelPort
    storage: LocalJsonlSettings | VolatileSettings | ProvidedStorage
    policy: Policy
    client_limits: ClientLimits
    model_limits: ModelExecutionLimits
    context_config: ContextConfig
    estimator: TokenEstimator
    specs: tuple[AgentSpec, ...]
    tools: tuple[Tool, ...] = ()
    identity_mode: Literal["caller", "application", "anonymous"] = "caller"
    resume: Literal["off", "report", "auto"] = "report"
    default_spec: str | None = None
    archive: ArchiveSource | None = None
    archive_sink: ArchiveSink | None = None

@dataclass(frozen=True)
class ClientPorts:
    providers: tuple[Provider, ...] = ()
    guards: tuple[ResultGuard, ...] = ()
    attempt_factory: Callable[[RequestTags], AttemptRecorder] | None = None
    audit: AuditSink | None = None
    tracer: Tracer | None = None
    context: ContextStrategy | None = None
    execution_limits: ExecutionLimits | None = None
    clock: Clock | None = None

class ClientHost:
    @classmethod
    async def open(cls, settings: ClientSettings) -> Self: ...
    @classmethod
    async def from_ports(
        cls, settings: ClientSettings, *, ports: ClientPorts,
    ) -> Self: ...
    def client(self, actor: Actor) -> ClientProtocol: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def aclose(self) -> bool: ...
    async def wait_closed(self) -> bool: ...

class ClientProvider(Protocol):
    def __call__(self, actor: Actor) -> ClientProtocol: ...
```

`ClientHost.open(settings)` acquires the explicit storage profile, assembles Tether's invariant engine from the core public interfaces in `ClientSettings`, starts recovery, and returns a ready host. `LocalJsonlSettings` opens the JSONL root, session store, and history provider using the documented implementation profile; the host derives `MessageHistory` from that history provider. Those resources and the derived adapter are host-owned. `VolatileSettings()` explicitly selects process-lifetime memory storage for fabricated demos; it creates no files and makes no restart-durability claim. It requires `resume="off"` and MUST reject recovery requests before admission. `ProvidedStorage` borrows both caller-supplied ports and derives a borrowed message-history adapter. `open()` performs only I/O implied by the selected explicit storage profile and closes partially acquired resources on failure; it performs no environment discovery or credential selection. The host is immediately usable and may be used with `async with`; `__aenter__` is idempotent for a ready host, while `__aexit__` closes it. `aclose()` works whether or not the context manager was used.

`from_ports(settings, ports=...)` adds explicit provider, guard, attempt, telemetry, context-strategy, execution-limit, or clock integrations covered by CONTRACT-001–005; it accepts no `Engine` instance. Both factories MUST reject contradictory resource/profile values and close resources they opened on failure. Every caller-supplied port is borrowed and is never closed by the host. Resources constructed by the host from `LocalJsonlSettings` are host-owned and are closed after workers drain. No caller-supplied port has an implicit ownership switch.

The current `Tether(engine=..., store=..., history=..., limits=...)` composition remains a compatibility path for existing callers. It is an advanced path and does not define the ordinary `tether.client` construction API. `resume="auto"` retains its existing explicit refusal until qualified. A host binds to its owning event loop; `open()` completes recovery before returning, and `aclose()` seals admission and drains owned workers. `aclose()` returns `False` while actual work remains and retains ownership until physical completion. After shutdown is initiated, `wait_closed()` awaits physical cleanup and returns its drain outcome; cancelling that waiter MUST NOT cancel the owned cleanup. Calling it before shutdown raises `tether_not_closing`. An interface that borrows a client or provider MUST NOT close the host. A standalone launcher MUST own and close its host.

The core public exports MUST re-export every referenced contract type needed to create `ClientSettings` and `ClientPorts`, including `LocalJsonlSettings`, `VolatileSettings`, and `ProvidedStorage`. The model, policy, specs, tools, and limits are explicit public inputs; no implicit model, policy, or credential defaults are selected. Credentials remain on the current actor supplied at request time and are never stored in settings. The reference embedder example MUST construct or select its storage/history profile through documented public APIs and import only from public `tether.*` modules. Construction, ownership, and failure-cleanup proof is required before implementation acceptance; legacy demo sequence evidence alone does not qualify the host.

Supported optional composition profiles are public modules: `tether.client.compatible` exposes `CompatibleModelSettings`, `CompatibleModel`, and `TokenizerSettings` for an explicit compatible endpoint; `tether.client.databricks` exposes `DatabricksCliSettings` and `DatabricksCliProfile` for an explicitly selected CLI profile. They construct private adapters behind typed public methods and select no endpoint or credentials implicitly. `tether.client.testing` provides `scripted_model`, `replay_model`, `fabricated_model_context`, `demo_policy`, and `fabricated_storage` for explicitly fabricated reference-client composition. `fabricated_storage()` is an asynchronous context manager yielding borrowed `ProvidedStorage` ports; its caller owns the context and closes all borrowing hosts before exit. It writes no files and qualifies neither restart durability nor external storage. Importing these modules performs no provider or credential I/O. Optional dependency requirements remain explicit. Reference clients use these supported factories instead of importing engine, store, adapter, or SDK implementations.

`Actor` is explicit per operation context. Its credential is neither copied into a browser store nor serialized to the wire. A client binds an actor identity and uses the current credential and policy on each operation. A client provider MUST return a fresh actor-bound facade when the application auth layer resolves a changed identity or credential.

### 2. Public client protocol

`ClientProtocol` is a `typing.Protocol` exported by `tether.client`. It exposes only transport-neutral Tether values and errors. Required signatures follow CONTRACT-003, with `link_history` from the current implementation and `turn_result` added for process-independent remote handle recovery.

```python
class ClientProtocol(Protocol):
    async def list(self, query: SessionQuery | None = None, *, tier: Literal["live", "archive"] = "live", cursor: str | None = None, limit: int = 50) -> Page[SessionSummary]: ...
    async def get(self, session_id: str, *, tier: Literal["live", "archive"] | None = None) -> SessionView: ...
    async def entries(self, session_id: str, *, tier: Literal["live", "archive"] | None = None, start: int = 0, limit: int = 100, reverse: bool = False) -> EntryPage: ...
    async def history(self, session_id: str, *, tier: Literal["live", "archive"] = "live", start: int = 0, limit: int = 100) -> HistoryPage: ...
    async def pending(self, session_id: str) -> PendingConfirmation | None: ...
    async def snapshot(self, session_id: str) -> SessionSnapshot: ...
    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage: ...
    async def call_detail(self, session_id: str, model_call_ids: Sequence[str]) -> tuple[CallDetail, ...]: ...
    def follow(self, *, session_id: str | None = None, since: int | None = None) -> AsyncIterator[Change]: ...
    async def find(self, ref: str) -> SessionSummary | None: ...
    async def create(self, *, ref: str | None = None, spec: str | None = None, title: str | None = None, tags: Sequence[str] = (), settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def send(self, session_id: str, text: str, *, request_id: str | None = None, attachments: Sequence[AttachmentRef] = (), spec: str | None = None) -> TurnHandle: ...
    async def confirm(self, session_id: str, invocation_id: str, answer: Answer) -> TurnHandle | None: ...
    async def resume(self, session_id: str) -> TurnHandle: ...
    async def resolve(self, session_id: str, invocation_id: str, outcome: Literal["completed", "not_completed"], *, reason: str) -> SessionSummary: ...
    async def cancel(self, session_id: str) -> bool: ...
    async def cancel_turn(self, session_id: str, turn_id: str) -> bool: ...
    async def update(self, session_id: str, *, title: str | Unset | None = UNSET, tags: Sequence[str] | None = None, settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def close(self, session_id: str) -> SessionSummary: ...
    async def restore(self, session_id: str) -> SessionSummary: ...
    async def link_history(self, session_id: str, message_index: int, previous: HistoryRef, verified: HistoryRef, *, key: str) -> Entry: ...
    async def turn_result(self, session_id: str, turn_id: str) -> TurnResult | None: ...

class TurnHandle(Protocol):
    @property
    def turn_id(self) -> str: ...
    async def result(self) -> TurnResult: ...
    async def cancel(self) -> None: ...
```

`turn_result` returns `None` only while a known accepted turn has no terminal result. An unknown turn raises `UnknownTurn`. It returns a terminal `TurnResult` through current owner and content authorization, or raises an explicit denied/unavailable error. It MUST NOT infer completion from process memory, a missing handle, an orphan history record, or a lost HTTP response. `cancel_turn` cancels only when `turn_id` is the current active turn; a stale identifier returns `False` and MUST NOT cancel a later turn in the same session. Both methods are required CONTRACT-003 revision dependencies before remote handle support is claimed.

The implementation MUST also export the public value and error types needed by these methods, including `Actor`, `Answer`, `AttachmentRef`, `Change`, `TurnResult`, `TurnHandle`, `UnknownTurn`, `SessionQuery`, `SessionSummary`, `SessionView`, `SessionSnapshot`, `HistoryPage`, `CallDetail`, `HistoryRef`, `Page`, `EntryPage`, and `EffectPage`. Web-framework types, internal `SessionService`, stores, and engine objects MUST NOT appear in `tether.client` consumer annotations.

### 3. Supported integration seams and capability negotiation

Advanced integrations use named typed ports already governed by CONTRACT-001–005, exported from the documented public modules. This contract adds no generic plugin registry. Each published port MUST identify ownership, async behavior, error behavior, and its conformance suite in its owning contract.

The optional `tether.tui` package MUST accept an injected `ClientProtocol` for embedded use and MUST also accept the HTTP-backed Python remote client without inspecting its implementation. The TUI borrows that client and never closes its host or transport. A standalone CLI launcher composes and owns its `ClientHost` or remote client, passes the client to the TUI, and closes owned resources after the UI exits. The demo remains a launcher fixture; mounting the TUI MUST NOT implicitly create a demo host.

An injected asynchronous credential provider is a public callable that yields the current `Credential` for one request or reconnect. The remote Python client calls it at each outbound request boundary and never caches its result as durable client state:

```python
class CredentialProvider(Protocol):
    async def __call__(self) -> Credential: ...
```

Remote Python clients are an optional extra of the core distribution under `tether.client`. They implement `ClientProtocol` against a caller-supplied endpoint and an injected async credential provider. The provider is called for each request and stream reconnect; credentials are never read from environment, persisted, or included in diagnostics. The base install does not import the remote transport dependency. A remote client closes only transport resources it constructs. API framework types and `tether_web_api` are not runtime dependencies of the remote client.

The API's versioned schema is the source for remote-client generation. Generated schema data is shipped with the remote extra; `tether_web_api` consumes the same source without being imported by the client. Package compatibility declares the accepted HTTP major.

```python
class ClientCapabilities(Payload):
    methods: Mapping[str, Literal["supported", "unsupported"]]
    tiers: Mapping[Literal["live", "archive"], Literal["available", "unavailable"]]

class ClientProtocol(Protocol):
    @property
    def capabilities(self) -> ClientCapabilities: ...
```

Capabilities are explicit and stable for one client configuration. Consumers MUST inspect capability state before offering optional archive, restore, payload detail, or history operations. The configured store must support both durability and fencing before `resume` is advertised as supported; process-only storage advertises it as unsupported. Unsupported operations raise the public `Unsupported` error with a stable reason. Missing, denied, or unavailable content is never encoded as an empty successful result.

### 4. API factory and settings

`tether_web_api` is a separate optional Python distribution and import namespace. It owns FastAPI and Pydantic HTTP request/response types. The core distribution does not depend on FastAPI.

```python
class Authenticator(Protocol):
    async def actor_for(self, request: Request) -> Actor: ...

class ApiSettings(Payload):
    api_prefix: str                  # absolute path, default /api/v1
    event_queue_size: PositiveInt
    event_batch_limit: PositiveInt
    heartbeat_seconds: PositiveSeconds
    request_timeout_seconds: PositiveSeconds
    allowed_origins: tuple[str, ...]  # explicit; empty means same-origin only

def create_api(
    *, clients: ClientProvider, authenticator: Authenticator, settings: ApiSettings
) -> FastAPI: ...

class ApiFactory(Protocol):
    def __call__(
        self, *, clients: ClientProvider, authenticator: Authenticator,
        settings: ApiSettings
    ) -> ASGIApp: ...

class UiSettings(Protocol):
    mount_path: str
    asset_root: str
    api_discovery_path: str

def create_static_app(*, settings: UiSettings) -> ASGIApp: ...

class UiFactory(Protocol):
    def __call__(self, *, settings: UiSettings) -> ASGIApp: ...
```

Authentication is application-supplied. There is no default authenticator, permissive actor, shared singleton, or secret read from environment. The `Request` type above belongs only to `tether_web_api`. Every HTTP request resolves the current actor and obtains a client through `clients(actor)`. An event stream rechecks actor and `READ_SESSION` before each bounded delivery batch and again before every message/history payload disclosure. Revocation terminates the stream without further data.

`UiSettings` and `create_static_app` belong to `tether_web_ui`; the settings configure the mount path, packaged-asset root, and API discovery path. The static app serves packaged assets under that mount and does not own or close the API, authenticator, client provider, or host.

### 5. Versioned REST surface

All routes use the configured prefix, default `/api/v1`. JSON request and response bodies are validated against OpenAPI schemas. Every existing `ClientProtocol` method maps to one endpoint or an explicit `unsupported` capability; parity cannot silently omit a method.

| Method | Path | Client operation |
|---|---|---|
| GET | `/capabilities` | `capabilities` |
| GET | `/sessions` | `list` with filters, tier, cursor, and limit |
| POST | `/sessions` | `create` |
| GET | `/sessions/by-ref/{ref}` | `find` |
| GET | `/sessions/{id}` | `get` |
| GET | `/sessions/{id}/snapshot` | `snapshot` |
| GET | `/sessions/{id}/entries` | `entries` |
| GET | `/sessions/{id}/history` | `history` |
| GET | `/sessions/{id}/pending` | `pending` |
| GET | `/sessions/{id}/effects` | `effects` |
| POST | `/sessions/{id}/call-details` | `call_detail` |
| POST | `/sessions/{id}/turns` | `send` |
| GET | `/sessions/{id}/turns/{turn_id}` | `turn_result` and acceptance state |
| DELETE | `/sessions/{id}/turns/{turn_id}` | `cancel_turn`; only the matching active turn may be canceled |
| POST | `/sessions/{id}/cancel` | `cancel`; cancels whichever turn is active at command time and returns its boolean result |
| POST | `/sessions/{id}/confirmations/{invocation_id}` | `confirm` |
| POST | `/sessions/{id}/resume` | `resume` |
| POST | `/sessions/{id}/effects/{invocation_id}/resolve` | `resolve` |
| PATCH | `/sessions/{id}` | `update` |
| POST | `/sessions/{id}/close` | `close` |
| POST | `/sessions/{id}/history-links` | `link_history` |
| POST | `/sessions/{id}/restore` | `restore` |
| GET | `/events` | `follow` as Server-Sent Events (SSE) |

Request and response schemas use Tether-owned HTTP wire models, not Python object dumps. `list` retains keyset cursor semantics. `entries`, `history`, and `effects` retain start/limit bounds and source status. `snapshot` is atomic. The call detail endpoint requires current `READ_CALL_PAYLOAD` in addition to session authorization for raw exchanges.

### 6. REST acceptance, handles, and idempotency

`POST /sessions/{id}/turns` requires a stable `request_id` even though the local method accepts `None`. On acceptance it returns HTTP 202 with `turn_id`, `request_id`, state `accepted`, and a relative result URL. The server MUST NOT wait for model completion before acknowledging acceptance, and client disconnect MUST NOT cancel accepted work. The remote handle's `result()` queries the result URL until terminal state using bounded asynchronous waits; cancellation by handle sends DELETE to the turn-specific path. `POST /sessions/{id}/cancel` implements local `cancel(session_id)` semantics and is distinct from stale-safe handle cancellation.

The API MUST retain no process-local operation table as outcome authority. It resolves accepted and terminal state through `ClientProtocol.turn_result` and persisted session facts. An unknown `turn_id` raises the public `UnknownTurn`; `None` means only that a known accepted turn is still nonterminal. A repeated send request ID with the same canonical request returns the same turn identity; unequal content, attachments, or requested spec returns a conflict. HTTP create requires an explicit request idempotency key and maps it to the local `ref`. Confirmation returns HTTP 202 and the accepted handle when `confirm()` returns a handle, or HTTP 200 with current state and `accepted: false` when it returns `None`; this is a no-op/late response, not a rollback. `cancel_turn` atomically checks the requested turn identity under session ownership so a late retry cannot cancel a successor turn. Only commands whose contract defines a durable idempotency key may be automatically retried. The adapter MUST NOT promise blanket idempotency for confirmation, resolution, close, cancellation, metadata update, or restore. After a lost response, callers use the command-specific reconciliation rule from CONTRACT-003/005; they do not assume failure or repeat an external effect.

Example acceptance:

```json
{
  "request_id": "req-fabricated-01",
  "turn_id": "turn-fabricated-01",
  "state": "accepted",
  "result_url": "/api/v1/sessions/s-fabricated/turns/turn-fabricated-01"
}
```

### 7. Feed and reconnect wire semantics

The event schema is a discriminated union generated or checked alongside OpenAPI. It includes `session_updated`, `session_removed`, `entries_appended`, `reply_text`, `reply_reset`, `lagged`, and `resync_required`, with the fields and meaning of CONTRACT-003. Unknown event discriminators are rejected by the runtime validator and cause reconnect from a fresh snapshot.

- Persisted events carry an owner-scoped commit `seq`, and may share a sequence within one commit. Each event carries `last_in_commit`; exactly one filtered event closes a nonempty commit. A consumer advances its durable `Last-Event-ID` only after that marker.
- Only the SSE frame for the event with `last_in_commit: true` has an `id` field. Intermediate frames, heartbeats, and provisional text/reset frames MUST omit `id`, preventing browser-managed `Last-Event-ID` from advancing across a partial commit or ephemeral output.
- `reply_text` carries `turn_id`, `attempt_id`, a Unicode scalar-value `offset`, and text. Offset counts Unicode code points from the start of the attempt; it is neither UTF-8 bytes nor JavaScript UTF-16 code units. The event has no durable sequence and is never included in the resume cursor. `reply_reset` identifies a discarded attempt and reason.
- `lagged` invalidates provisional text. `resync_required` uses `too_old` or `gap`; the client obtains a fresh atomic snapshot, reads entries through its captured head, then reconnects after its watermark.
- Streams have finite queue, batch, heartbeat, and byte limits from `ApiSettings`. Slow consumers are disconnected or receive lag/resync; they cannot delay engine commits or other consumers.
- Authorization is current at each batch. Authentication expiry, policy denial, or owner change closes the stream and requires a new authenticated connection.

### 8. Wire types, generation, and compatibility

OpenAPI 3.1 JSON schemas are the authority for REST types. The event union is a linked JSON Schema source with a checked mapping to OpenAPI. `@tether/client` types are generated from these sources and include runtime validators. CI checks a clean generation, verifies no uncommitted generated diff, and tests every event discriminator.

Python integers remain integers. To preserve exact values in JavaScript, owner sequence, entry position, token usage, and other potentially 64-bit counters are decimal strings on the JSON/SSE wire with an integer-string format. TypeScript parses them as `bigint` at the transport edge and serializes them back losslessly. IDs, cursors, opaque refs and request IDs remain strings. UI code MUST NOT compare or add these values as JavaScript `number`.

The `/api/v1` path freezes field meaning and event semantics for its major version. Additive optional response fields and new event discriminators require a documented forward-compatibility policy; unknown variants force safe resnapshot. Removing a field, narrowing an enum, changing cursor or ordering semantics, or reusing a discriminator requires `/api/v2`. The server publishes its OpenAPI document at a configured path. Browser package release versions are independent from the HTTP major but declare the supported API major range.

### 9. Browser packages and rendering

- `@tether/client` has no React import or dependency and receives an explicit base URL, fetch implementation, and credential mode.
- `@tether/react` declares React as a peer dependency and receives an explicit client through a provider or hook argument. It creates no singleton or ambient credential store.
- `@tether/ui` exports typed composable components and stable layout and message-rendering slots. Integrators may replace those slots without private imports.
- The reference app composes all three packages. It is an example and integration proof, not an auth provider or deployment service.
- Message text is rendered as plain text by default. A custom renderer receives typed content and must opt into safe formatting; arbitrary HTML is never trusted. Tool arguments and model exchanges are shown only through the authorized call-detail route.
- TanStack Query is the single server-state cache. One reducer applies committed feed events into that cache; no separate durable UI state may establish a second event-order authority. Provisional attempt text remains ephemeral and is cleared on reset, lag, failure, identity change, and resnapshot.
- `tether_web_ui` is a separate optional Python distribution and import namespace. It includes built browser assets in its wheel and supports a configurable mount path and reverse-proxy asset prefix. The default same-origin mode is supported. Cross-origin credentials require an explicit origin allowlist.

### 10. Security and cross-origin behavior

The host application owns authentication and supplies the authenticator. The API MUST NOT invent auth defaults. Browser commands use same-origin credentials by default. Cookie-authenticated state-changing requests require CSRF protection using an application-supplied token or equivalent verified origin mechanism. CORS with credentials requires exact configured origins; wildcard origins with credentials are forbidden. API and UI packages never log cookies, authorization headers, request bodies, message text, tool arguments, or model payloads.

## Error Semantics

HTTP errors use RFC 9457 Problem Details plus stable `code`, `retryable`, and optional `retry_after_seconds` fields. Error detail is content-free.

| Condition | HTTP result | Retry and recovery |
|---|---|---|
| Invalid request or range | 400 `invalid_request` / `read_limit` | Correct request; do not retry unchanged |
| Missing or invalid authentication | 401 `authentication_required` | Authenticate again |
| Current policy denies owner/session/payload | 403 `denied` | No content returned; retry only after authority changes |
| Session or turn is absent | 404 `unknown_session` / `unknown_turn` | Re-list or obtain current state |
| Request key reused with different body; conflicting resolution | 409 `request_conflict` / `resolution_conflict` | Read current state and use a new intentional command |
| Closed session or active turn conflict | 409 `session_closed` / `turn_in_progress` | Wait or select an allowed action |
| Body or event exceeds a configured bound | 413 `limit_exceeded` | Reduce page or payload size |
| Unsupported optional method/profile | 501 `unsupported` | Inspect capabilities |
| Rate/capacity limit | 429 `capacity_limited` | Retry after supplied delay, with the same idempotency key |
| Provider or accepted result is temporarily unavailable | 503 `unavailable` | Preserve request ID; retry or read status; never infer command failure |
| Command acceptance may have committed but response outcome cannot be confirmed | 503 `outcome_uncertain` with request/turn locator when authorized | Reconcile through the command-specific result/status operation; do not dispatch again |
| Feed position expired or gap detected | SSE `resync_required` | Snapshot and reconnect |

History pages preserve per-slot `pending`, `denied`, `unavailable`, `missing`, `partial`, `unsupported`, and integrity states. They do not return a successful empty substitute. A whole request denied by current policy uses 403 and carries no content. A 401/403 before command acceptance means the command was not accepted; after acceptance it MUST NOT imply rollback. If current authorization blocks returning acceptance or result state, the server returns a content-free `outcome_uncertain` locator where policy permits, or a content-free denial without claiming the command was rejected. A timeout after command acceptance uses a request ID and result URL to resolve outcome under current authorization.

## Examples

The ordinary embedder chooses a public resource profile and supplies the behavior it owns. These names are proposed exports; implementation must establish their exact imports and settings defaults against the current ports.

```python
from pathlib import Path
from tether.client import ClientHost, ClientSettings, LocalJsonlSettings

settings = ClientSettings(
    model=application_model,
    storage=LocalJsonlSettings(root=Path("./tether-data")),
    policy=application_policy,
    client_limits=client_limits,
    model_limits=model_limits,
    context_config=context_config,
    estimator=estimator,
    specs=(support_spec,),
    tools=application_tools,
)
host = await ClientHost.open(settings)
try:
    client = host.client(actor)
    sessions = await client.list()
finally:
    await host.aclose()
```

An application with an existing store and history provider uses `ProvidedStorage(store=store, history=history)`. Those ports remain caller-owned after host shutdown. An API mount receives a client provider and authenticator explicitly; it does not select identity or create a shared actor implicitly.

## Precedence and compatibility

PRD and FEAT-013/014/015 govern user behavior. Architecture and accepted ADRs govern module ownership and technology selection. CONTRACT-003/005 govern core semantics; this contract extends them only for named additive remote surfaces. OpenAPI/event schemas govern wire representation. Generated TypeScript is derivative and MUST be regenerated, never edited as an independent authority. Exact pins and package version ranges are released only after compatibility evidence. A behavior change to core client methods requires a CONTRACT-003 revision; HTTP-only representation changes follow this major-version rule.

## Executable contract checks

Implementers MUST be able to derive checks for: a conforming dummy `ClientProtocol`; every protocol method's route/capability mapping; owner and current-policy checks; request replay/conflict; lost response after acceptance; journal-backed result after API restart; stale-turn cancellation refusing a successor; `last_in_commit` watermark advancement; lag, reset, too-old, and gap recovery; exact 64-bit round trips; provider unavailability and denial; CSRF/CORS settings; safe message rendering; and packaged asset resolution under a proxy prefix.

## Non-normative notes

The current `Client` is concrete, and `Tether` composes an `Engine`, live `SessionStore`, `HistoryProvider`, and `ClientLimits`. `ClientProtocol`, `ClientCapabilities`, `turn_result`, `tether_web_api`, `tether_web_ui`, and the browser packages are proposed surfaces, not shipped support. Existing client and store suites establish only their named local behaviors. API, browser, proxy, authentication, and independent embedder behavior require the proof gates in FEAT-014/015 and TP-002.
