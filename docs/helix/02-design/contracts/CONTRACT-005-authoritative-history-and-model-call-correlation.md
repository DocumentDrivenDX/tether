---
ddx:
  id: CONTRACT-005
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: ADR-019
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-004
      kind: informed_by
    - id: TP-001
      kind: informs
---

# CONTRACT-005: Authoritative History and Model-Call Correlation

**Contract ID**: CONTRACT-005
**Type**: library (asynchronous Python ports and persisted references)
**Version**: v1, initial pre-release design
**Related**: ADR-013/018/019/020/021; CONTRACT-001 to CONTRACT-004; PRD FR-50 to FR-53

## Purpose

Separate three authorities: Tether's execution journal for order, approvals and effects; a replaceable history provider for original content and actual model exchanges; and derived model context. The default history provider writes local JSON Lines (JSONL). An AI Gateway provider resolves model exchanges from inference tables without requiring a second full local payload copy.

## Scope and Boundaries

- In scope: immutable content references, local JSONL history, externally recorded exchanges, per-attempt correlation, bounded authorized reads, supplemental content and failure/recovery semantics.
- Out of scope: replacing the execution journal with an inference table, control over third-party delivery/retention, reconstructing a full conversation from repeated prompts, or assuming a tracing id is an authorization grant.
- Owner: Tether core owns reference/correlation types and mediation. Providers own persistence, decoding and vendor translations. A provider may internally compose read-only and writable backends behind one history interface.

## Normative Surface

Types are frozen Pydantic models with `extra="forbid"`. Digests are SHA-256 of versioned canonical content: sorted JSON keys, UTF-8, no insignificant whitespace, and no credential/transport callback fields. Provider configuration is explicit. Every read/write and callback that can wait is asynchronous.

### 1. Content and authority

```python
class HistoryRef(BaseModel):
    provider: str; key: str; codec: str
    owner: str; session_id: str
    kind: Literal["message", "model_exchange", "summary"]
    sha256: str                                  # expected canonical value, never a credential
    component: Literal["record", "request", "response", "assistant", "summary"] = "record"

class HistoryRecord(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["message", "model_exchange", "summary"]
    message: Message | None = None
    summary: str | None = None
    request: JsonValue | None = None; response: JsonValue | None = None

HistoryStatus = Literal["ready", "pending", "denied", "unavailable", "missing",
                        "partial", "ambiguous", "unsupported", "integrity_error"]
class HistoryValue(BaseModel):
    ref: HistoryRef; status: HistoryStatus
    record: HistoryRecord | None = None
    reason: str | None = None; retry_after_seconds: float | None = None
    source_locator: dict[str, str] = {}            # allow-listed opaque provenance, not credentials

class HistoryCapabilities(BaseModel):
    writable: bool; externally_recorded: bool
    durability: Literal["none", "process", "os", "external"]
    delivery: Literal["immediate", "eventual"]
    coverage: frozenset[str]                      # message, model_exchange, summary

class HistoryProvider(Protocol):
    id: str; capabilities: HistoryCapabilities
    async def put(self, *, actor: Actor, session_id: str, key: str,
                  record: HistoryRecord) -> HistoryRef: ...
    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]: ...
    async def bind_exchange(self, *, actor: Actor, binding: ExchangeBinding) -> ExchangeRefs: ...
```

For `message` only `message` is set; for `summary` only `summary` is set; for `model_exchange`, request and response are separate optional components whose observed availability is explicit. A `ready` value has the complete requested canonical record and matching digest. `partial` may expose only independently verified available components; it MUST NOT be treated as ready. Other states carry no fabricated content. The same key with unequal canonical content raises `HistoryConflict`; identical writes are idempotent. Unknown codecs/versions are `unsupported`, never guessed.

A codec can expose a normalized assistant message or summary component of one exchange. `bind_exchange` returns both the exchange ref and the observed normalized source ref with its own kind/component/digest. The journal commits both relevant refs; a client cannot manufacture a new component selector to broaden access. Local `put` idempotency applies to complete record keys; component identity is `(provider,key,codec,component,sha256)`. No unobserved component is minted as ready.

The journal's immutable message entry contains a `HistoryRef`, role and stable message id. Its dense entry position remains the source address used by CONTRACT-004. Content lives behind that reference. The client materializes a history page by joining committed entries to the history provider; its ordering always comes from journal positions, not provider timestamps or table row order. Uncommitted orphan history records are not visible as accepted messages.

`HistoryRef` is not a bearer capability. The service first checks session ownership and `READ_SESSION`, verifies every ref is reachable from the requested session's committed entries, and calls the provider as the present actor. Provider reads MUST enforce owner/session scope as well. The public client takes session positions or known call ids, never arbitrary table names, SQL, row selectors or external request ids as access grants. Denied reads never fall back to an application identity with broader rights.

### Canonical digests and immutable bindings

Canonical record bytes are UTF-8 JSON from all declared HistoryRecord fields, including explicit nulls, with sorted object keys, array order preserved, no insignificant whitespace and no non-finite numbers. JSON encoding preserves Unicode code points without normalization. The reference encoding is json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")); implementations must match its bytes for the supported JSON value domain. A codec declares the corresponding message/payload serialization; unknown codec versions refuse decoding. The hash is over these complete record bytes, not a concatenation of request/response hashes.

ExchangeBinding.exchange_sha256 is computed from the actually observed exchange HistoryRecord before binding. source_sha256 is computed from the separately normalized HistoryRecord(kind="message", message=...) or HistoryRecord(kind="summary", summary=...). It is required exactly when response_kind is assistant/summary. Request/response digests retain their codec-defined wire meanings; they cannot substitute for record hashes. A provider verifies the decoded expected record before ready, including normalized components. Partial availability never proves a complete exchange digest.

A binding uses an immutable provider key incorporating attempt identity, capture/component phase, codec version and expected record digest. An incomplete observation and a later complete observation get different keys/refs; no existing expected digest or record is rewritten. Binding an identical observation is idempotent. Later receipts/observations are appended as facts. An unobserved response has no assistant/summary source ref; reconciling a prepared attempt never itself commits a successful reply, dispatches a tool or converts a missing response into observed success.

### 2. Journal/content commit protocol

For locally written content, the engine calls `put` with a stable logical key and waits for the provider's declared durability before committing its reference, execution facts, summary projection and feed records in the live-store transaction. An orphan from a successful content write and failed journal commit is harmless and reusable by that same key after authorization; it is not a conversation event. Garbage collection requires proof that no committed reference or in-progress preparation needs it. A failed content write prevents acceptance/dispatch of the dependent step. There is no cross-backend atomic-transaction claim.

For an externally recorded exchange, the engine records a bounded `model_attempt` fact BEFORE dispatch, then appends the observed receipt/digests and immutable binding after response. `bind_exchange` returns exchange/source references whose content can remain `pending`; the journal commit certifies the observation and correlation, not third-party persistence. A same-process working set may use the already observed response to complete the current turn. After restart, unavailable required content produces `history_pending`/`history_unavailable`, never a guessed replay or a fresh unsafe effect. A gateway response followed by a failed binding commit leaves a prepared attempt whose unique outbound tags permit later reconciliation; no model response or tool is silently marked committed.

The writable provider MUST be at least as durable as the selected restart guarantee for all content needed for that guarantee. With an external provider, guarantees are conditional on its coverage/availability: the journal can recover effects and pending interaction while source text is unavailable. The UI still shows the session and its metadata, with explicit holes. No claim of complete/offline conversation recovery is made from an eventual provider alone.

Content caches and pending-approval materializations are derived, bounded copies with original refs/digests and declared lifetimes. They MUST NOT become competing authorities or silently substitute for denied/mismatching source content. No default permanent full shadow copy of Gateway payloads is required. Deployments requiring offline or retention-independent content select an explicit mirror/writable provider and qualify it.

### History-link resolution

history_linked names an original committed ref, replacement ref and verified canonical digest/component equivalence. The owner/session, content kind, semantic codec and canonical decoded content must match. The engine authorizes and verifies both refs before appending the binding under the session lease. A failed/unknown append cannot authorize eviction. Different wire storage codecs may link only through an explicit verified semantic decoder; a request-only record cannot link to a richer exchange as though they were equal content.

Reads first authorize the original entry, then resolve only links reachable in that session's accepted journal snapshot. The highest committed journal position is the active replacement for a ref; all earlier links remain provenance. Resolution follows a configured finite hop bound, rejects cycles/conflicting equivalence with integrity_error, and verifies each selected ref and present actor. Missing/unavailable/denied replacements remain explicit states; there is no automatic fallback to stale originals, mirrors or earlier bindings. A deployment wanting another source appends a newly authorized verified link. Retrying link append reuses its idempotency key; independently accepted later links have distinct keys.

Snapshots pin the binding head used by a read. Concurrent linking cannot mix generations within a page. Mirror eviction additionally requires durable accepted linkage and provider-specific retention proof. Content digests in original entries remain unchanged; journals and clients never mutate refs to hide source changes.

### 3. JSONL default

`JsonlHistory(root, limits, durability)` implements all record kinds and is the default history provider for reference clients, alongside the separately selected execution journal. The root is an explicit composition-root path; the core reads no environment. It stores one versioned canonical envelope per UTF-8 line, including owner/session/key/kind/codec/digest and record. File names are internal opaque encodings; caller strings cannot become paths. No authorization headers, credentials, SDK objects or transport callbacks are serialized.

Writes are serialized by one writer per root, with process ownership enforced by an operating-system lock; a second writer is refused with `HistoryBusy`. Blocking file/lock/fsync work is offloaded. The provider declares only the crash durability its fsync/directory handling actually provides. A complete line is the content commit unit; a torn final line is quarantined as an uncommitted fragment and reported in recovery, never decoded as a valid record. Earlier referenced records are never silently truncated or repaired. A checksum mismatch or corruption inside a committed record is `integrity_error` and fails dependent reads.

An offset/key index is rebuildable by bounded-memory streaming at open/recovery and maintained with writes. Normal keyed reads use that index; session lists/snapshots never scan JSONL. Index crash/rebuild does not change content identity or source positions. Append/reopen, same-key conflicts, concurrent writer refusal, partial writes, permissions, restart and corrupt-index recovery are conformance cases. The default is a history provider, not a claim that plain JSONL supplies the live store's atomic summaries, leases and change feed.

### 4. Correlation and attempts

```python
class AttemptIdentity(BaseModel):
    session_id: str; turn_id: str; model_call_id: str; attempt_id: str
    purpose: Literal["answer", "summary", "correction"]
    trace_id: str | None = None; span_id: str | None = None

class ResponseReceipt(BaseModel):
    provider: str; endpoint: str; schema_profile: str
    provider_request_id: str | None = None
    provider_invocation_id: str | None = None
    response_id: str | None = None
    request_id_source: Literal["header", "body", "none"]

class ExchangeBinding(BaseModel):
    identity: AttemptIdentity; receipt: ResponseReceipt
    owner: str; request_digest: str; response_digest: str | None
    exchange_sha256: str                         # hash of the observed canonical HistoryRecord
    source_sha256: str | None = None               # hash of normalized message/summary HistoryRecord
    codec: str                                   # versioned decoding/extraction rule
    response_kind: Literal["assistant", "summary", "none"]

class ExchangeRefs(BaseModel):
    exchange: HistoryRef
    source: HistoryRef | None = None              # normalized assistant/summary component when observed

class AttemptRecorder(Protocol):
    async def reserve(self) -> AttemptIdentity: ...
    async def start(self, identity: AttemptIdentity, *, request_digest: str,
                    wire_request: JsonValue, codec: str = "model-port-v1") -> None: ...
    async def observe(self, identity: AttemptIdentity, receipt: ResponseReceipt, *,
                      wire_response: JsonValue | None = None) -> None: ...
```

The recorder is also the explicit authorized capture path for a local exchange provider: adapters pass their credential-free wire request/response representations under a named codec. JSONL persists them through HistoryProvider; Gateway mode records correlation/expected digests without creating a mandatory second payload store. Stream codecs state whether they capture terminal assembly, raw chunks or both and may not label a partial capture complete. ContextUse.request_digest describes the pre-adapter model request; ExchangeBinding.request_digest describes the codec-qualified wire request. Neither digest is substituted for the other.

Tether allocates a logical `model_call_id` for each answer, summary or output-correction generation. Every physical network attempt has a different `attempt_id`; retries retain the logical call id and purpose. The recorder is bound to the current actor, turn lease, tier and request purpose. `reserve` allocates the unique identity bound to the current actor/lease; reservation alone never authorizes egress. The adapter renders the exact wire representation with those ids before calling `start(identity, ...)`. `start` verifies the reserved identity and digest, authorizes current usage, and commits the prepared-attempt fact BEFORE egress. Every retry reserves another id and repeats the process. A failed or uncertain start forbids egress; reservation ids are never recycled. This avoids hashing a payload before its required attempt tags exist. `observe` records receipt metadata promptly, including on failures/stream headers when available. Cancellation/unknown network outcomes do not erase the prepared attempt. Client `send(request_id=...)`, Tether tool `invocation_id`, model-call/attempt ids, provider request ids and provider invocation ids are different namespaces.

`ModelRequest.attempts` is the engine's excluded recorder, not persisted content. Adapters/doubles use it for every actual attempt. An SDK's implicit transport retries must be disabled unless its hook can execute this recorder before every egress. All retry/correction/summary attempts count toward shared turn policy and finite request deadlines; emitting text prohibits hidden network restart as in CONTRACT-001. Pydantic conversation/run ids may be recorded as additional SDK metadata but never replace these engine ids or prove an external join. The selected Agent runtime bridge converts committed source history into transient SDK model history; it does not independently persist a second canonical transcript.

Trace/span ids are present only for real trace context. An untraced request still has stable session/turn/call/attempt ids. `traceparent` is propagation, not a history lookup grant or proof that a provider logged the trace. A local authorization refusal raises ModelAttemptDenied; policy exceptions normalize to denial. Journal StoreError/CommitUnknown/LeaseLost propagate without conversion to provider errors, and all prevent dispatch. The receipt keeps body completion id separately from the response header id; a body id MUST NOT silently be promoted to a proven inference-table key.

### 5. Gateway-backed provider

`GatewayHistory` implements the same history interface by composing a read-only inference-table reader with a required writable supplement for content the gateway never receives: accepted but unsent input, locally denied/cancelled turns, tool outputs omitted from context, local resolution notices, and other application-only messages. The supplement may use JSONL or another explicitly selected writable history provider. Thus selecting Gateway replaces authority for model exchanges without pretending the external service recorded every conversation event.

Original human/tool history and the exact model request are different records. A gateway request contains a bounded/redacted/compacted projection and may repeat earlier messages; it MUST NOT replace the original transcript or be appended as a new copy of every embedded message. An assistant or summary may reference a verified normalized component of a Gateway response via a versioned codec. If a local supplemental record is later rebound to equivalent remote content, append a verified `history_linked` fact with matching canonical digest; never mutate the original entry. Optional mirror eviction waits for proof of exact coverage and follows explicit retention policy.

The Databricks profile sends opaque `tether_session_id`, `tether_turn_id`, `tether_model_call_id`, `tether_attempt_id`, and `tether_purpose` values in the documented `Databricks-Ai-Gateway-Request-Tags` JSON HTTP header. Tags contain no content or credentials. Required ids are never lossy-sanitized or silently truncated; over-limit metadata refuses dispatch. Optional bounded User-Agent attribution and real `traceparent` remain useful secondary signals. The exact selected endpoint, cloud and schema profile are recorded; legacy serving endpoint `client_request_id`/`databricks_request_id` shapes are not substituted for current Gateway `request_id`/`invocation_id` without separate qualification.

Lookup uses configured table locators and parameterized predicates over endpoint/workspace, provider request id when known, and exact outbound attempt tags. It returns all relevant invocation rows. It deduplicates identical deliveries by qualified source identity and canonical payload digest, preserves different provider invocations, and reports incompatible duplicates or multiple possible response matches as `ambiguous`; it never selects the first row or newest timestamp as truth. Receipt, requester/endpoint scope, tags, schema and expected normalized digests are cross-checked before `ready`. Header-to-table equality and tag round trips require a fabricated live qualification; source code/unit tests alone are insufficient.

External recording coverage remains explicit: pending rows, expired retention, missing bodies, logging errors, schema changes, permission failures and ambiguous joins cannot become empty history or successful recovery. There is no write, migration or schema mutation against the inference tables. Table discovery, SQL reads, caches and queued work have finite configured bounds and are isolated by actor/credential context. A credential change/revocation invalidates read-cache authority; failures do not trigger a service-principal fallback.

## Precedence and Compatibility

- `HISTORY_CONTRACT_VERSION=1`; the initial pre-release CONTRACT-001 to CONTRACT-004 are revised together. Existing external histories are imported only through explicit versioned refs/codecs, never assumed to match the latest schema.
- The journal owns order/execution; the selected history binding owns content; CONTRACT-004 owns the derived model projection. A disagreement is an integrity/coverage problem, never a majority vote between copies.
- Local JSONL record version, content codec and external schema profile are independently versioned. Unknown fields are preserved where opaque payloads permit it; unknown semantic codecs refuse use. History provider migration appends explicit bindings and preserves identities/provenance.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| Delivery/read not finished | `pending` with optional finite retry hint | Bounded later read | Metadata remains usable; do not resend the model call |
| Caller denied | `denied` | After authorized identity/grants change | No authority fallback or cached disclosure |
| Source down / read deadline | `unavailable` | Configured bounded policy | Never empty history |
| Known retention gap / completed absence check | `missing` | Not automatic | Explicit gap; a timeout alone cannot prove permanent absence |
| Only one component present / logging error | `partial` | Later read when appropriate | Retain provenance and missing-component state |
| Several unequal candidate rows | `ambiguous` | No blind retry/selection | Qualification or explicit source reconciliation |
| Wrong digest/owner/codec/schema | `integrity_error` / `unsupported` | No | Never show another session or silently decode |
| Writer contention / key conflict | `HistoryBusy` / `HistoryConflict` | After contention / no | Preserve existing content |

A dependent engine request ends `context_unavailable` with `history_pending` or `history_unavailable` and no unproved effect. The recovery journal can still report/resolve an unknown effect without payloads. Archive failure remains independent of a turn; authoritative history needed to prepare the next context is a separate, explicit bounded dependency.

## Examples

```python
tether = Tether(engine=engine, store=live_store,
                history=JsonlHistory(root=history_root, limits=history_limits,
                                     durability="process"))
# Replace model-exchange authority; preserve native messages and execution facts:
tether = Tether(engine=engine, store=live_store,
                history=GatewayHistory(reader=inference_reader,
                                       supplement=local_history))
```

Turn `turn-a` makes answer call `model-a` with attempts `attempt-1` and `attempt-2`, then summary call `model-b` with `attempt-3`. Three prepared facts and distinct outbound tags precede egress. Missing payload for attempt-1 is not another reason to retry. A repeated request id with distinct provider invocation ids remains several source observations until an exact matching response is verified.

## Conformance and Qualification

`history_provider_suite(factory)` and `InMemoryHistory`/`ScriptedHistory` dummies cover immutable/idempotent content, ownership, ordered batch results (one value per requested ref), explicit states, bounded reads, corrupt/partial data, provider/codec isolation, and exclusion of credentials. JSONL adds torn-write/reopen/index/writer tests. `correlation_suite` proves prepared-before-egress, per-attempt ids, local wire capture and Gateway no-shadow-copy mode, partial headers/errors/cancellation, disabled hidden SDK retries and shared budgets. `gateway_history_suite` injects delayed/duplicate/multi-invocation/schema-shifted/denied/partial rows and out-of-order timestamps. A live fabricated profile proof checks actual request tags, header/body identities and source rows before any supported Gateway correlation claim.

## Non-Normative Notes

Current official Databricks documentation describes request/invocation ids, request tags, schema version, logging errors and at-least-once delivery; rows can be delayed and some payload/error records omitted. These are provider limitations, not Tether settings. [Inference tables](https://docs.databricks.com/aws/en/ai-gateway/inference-tables), updated 2026-09-17, and [request tagging](https://docs.databricks.com/aws/en/ai-gateway/usage-tracking#tag-requests-for-usage-tracking), updated 2026-10-16, accessed 2026-10-22. Qualification must name the actual workspace/cloud/profile; no live integration was run for this design.

The inspected downstream implementation supplies a reusable pattern: capture response-header ids separately from body ids, retain turn/span/call facts, render bounded session attribution, query only known call ids using parameterized SQL, isolate readers by caller context and bound slow warehouse reads. Its source marks header-to-table equality unverified and currently collapses repeated request ids; that behavior needs the multi-invocation/conflict handling above before reuse. Its blocking I/O, environment/context-global wiring and platform-specific names must be removed during a scrubbed port with adapted regression tests.
