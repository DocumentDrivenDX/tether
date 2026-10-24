---
ddx:
  id: CONTRACT-003
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-004
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
    - id: ADR-018
      kind: informed_by
    - id: ADR-019
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-002
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

# CONTRACT-003: Sessions, Stores, and the Client API

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-003
**Type**: library (Python ports and API)
**Version**: v2
**Related**: ADR-001, ADR-004, ADR-005, ADR-010, ADR-011, ADR-014, ADR-015, ADR-018, ADR-019, ADR-020, CONTRACT-001, CONTRACT-002

## Purpose

This contract fixes the execution journal and its live/archive ports, with original content behind the independent authoritative HistoryProvider (CONTRACT-005), and the client API through which a user interface lists sessions, reads session data, follows live changes, and gives commands. The client API is the boundary between tether and any interface (ADR-004); the live store is the boundary between tether and its hot storage (ADR-005); the archive is the boundary between tether and cold or derived history (ADR-019). All are asynchronous (ADR-001). It also states what each store guarantees, by declared capability, so a recovery guarantee is never claimed for a store that cannot keep it.

## Scope and Boundaries

- In scope: the entry types; summaries and views; the live store port, its capabilities, and the turn handle; the archive ports; the change feed and its resume rules; queries and cursors; the client API and its commands; references and idempotent sends; ownership and error rules; performance rules; the store and archive conformance suites.
- Out of scope: the engine's behavior (CONTRACT-002); the model port (CONTRACT-001); any wire format of a server adapter; the storage schema of a particular store; the visibility policy beyond the reservation in section 5.
- Owning system or team: tether core. A changed signature or rule is a contract version change.

## Normative Surface

Signatures are Python 3.11. Payload types are frozen `pydantic` models with `extra="forbid"`. MUST, MUST NOT, SHOULD, and MAY are used as defined in RFC 2119.

### 1. Entries

```python
Cursor = str                                          # opaque; produced and consumed by the store
Seq = int                                             # per-owner change sequence, increasing

class Entry(BaseModel):                               # a discriminated union on `kind`; immutable once written
    index: int                                        # dense position within the session, from 0
    at: datetime; turn_id: str | None
# NewEntry: the same fields as the kind below, without `index` and `at` (the store assigns them), plus an optional `key`
#   (idempotency, below); `turn_id` is supplied by the engine.
# kinds:
#   message          message_id: str; history_ref: HistoryRef; role: str
#                    call_manifest: tuple[CallManifest, ...]; tool_call_id: str | None; is_error: bool
#   model_attempt    identity: AttemptIdentity; request_digest: str; tier: str; status: Literal["prepared"]
#   model_receipt    identity: AttemptIdentity; receipt: ResponseReceipt
#   history_linked   message_index: int; previous: HistoryRef; verified: HistoryRef; canonical_digest: str
#   turn_started     spec_id: str; spec_version: str; spec_digest: str | None; trace_id: str | None; resumes: str | None
#   turn_ended       stop_reason: StopReason; reason: str | None
#   model_call       tier: str; request_id: str | None; status: Literal["ok", "error"]; duration_ms: int
#                    model_call_id: str; attempt_ids: tuple[str, ...]; receipt: ResponseReceipt | None
#                    exchange_ref: HistoryRef | None
#                    purpose: Literal["answer", "summary", "correction"]; finish_reason: str | None
#                    usage: TokenUsage | None; scope: dict[str, tuple[str, ...]]      # the scope values held at the call (identifiers)
#   tool_call        invocation_id: str; call_id: str; tool: str
#                    status: Literal["started", "ok", "error", "denied", "withheld", "invalid_arguments", "interrupted"]
#                    duration_ms: int | None; reason: str | None
#                    effect: Literal["read_only", "effectful"]; replay: Literal["safe", "unsafe"]
#                    tool_digest: str | None; args_digest: str | None; approval_index: int | None    # on `started`
#   confirmation     invocation_id: str; tool: str; state: Literal["requested", "approved", "declined", "superseded", "expired", "invalid"]
#                    args_digest: str; tool_digest: str; spec_digest: str; by: str | None; expires_at: datetime | None
#   resolved         invocation_id: str; outcome: Literal["completed", "not_completed"]; by: str; reason: str
#                    evidence: Literal["operator", "reconciler"]
#   settings         changes: dict[str, JsonValue]
#   session          event: Literal["created", "closed", "titled", "tagged"]; value: JsonValue | None
#   compaction       record: CompactionRecord       # generated summary ref and provenance, CONTRACT-004
#   context_use      use: ContextUse                 # source selection, estimates and summary references, CONTRACT-004
#   x.<provider>.<kind>   reserved for providers; no writer in v1
```

| Rule | Requirement |
|------|-------------|
| Immutable | An entry MUST NOT change after it is written. A rewrite of history is a new session forked from an entry. |
| Positions | `index` is dense and increasing within a session. The store assigns it on append. |
| Facts, not content | `model_call`, `tool_call`, and `confirmation` entries carry facts (identifiers, digests, tiers, statuses, sizes, reasons) and never message or tool content; content is in the selected history provider; `message` entries carry immutable refs and non-content call manifests. `CallManifest` contains call position, tool-call id, tool name, pinned tool digest, effect/replay declarations and canonical raw-proposal argument digest; it never contains argument values. A credential is in no entry. |
| Invocation id | `invocation_id` is derived by the engine from the session id, the index of the committed assistant entry that holds the call, and the call's position in it, so it is identical on every attempt and across resume (ADR-014). It is not the model's `call_id`, which can repeat. |
| Idempotency keys | An append MAY carry a `key`. The store MUST ignore an append whose `(session, key)` exists and return the existing entry. The engine uses `intent:<invocation_id>`, `result:<invocation_id>`, `approval:<invocation_id>`, `resolution:<invocation_id>`, and `request:<request_id>`. Summary and context-use keys include turn id and a monotonic preparation ordinal, allocated under the lease; retries of the same append reuse its key. A key with unequal payload is `EntryConflict`, not silent acceptance. A model-attempt key uses its globally unique `attempt_id`; history-link changes are new entries, never rewritten refs. |
| What keys do not do | A key prevents a duplicate *entry*. It does not prevent a duplicate external action, which is governed by the intent entry and the tool author's idempotency (ADR-014). |
| Unknown kinds | A reader MUST ignore an entry kind it does not know and pass it through. |

### 2. Summaries, views, queries, and pages

```python
class SessionSummary(BaseModel):                      # small; kept current by the store on every write
    id: str; owner: str; ref: str | None; title: str | None; tags: tuple[str, ...]
    status: Literal["idle", "running", "waiting", "interrupted", "closed"]
    resumable: bool                                   # an interrupted turn exists and the resume chain is within its limit
    tier: Literal["live", "archive"]; partial: bool   # `partial` for a session read from a derived archive
    created_at: datetime; updated_at: datetime; last_turn_at: datetime | None
    entry_count: int; message_count: int; turn_count: int
    preview: str | None                               # off by default; separately authorized metadata only
    usage: TokenUsage                                 # sum of known provider-reported usage
    usage_complete: bool                              # false if any accepted attempt has unknown usage
    active_turn_id: str | None; unresolved_effect_count: int
    spec_id: str | None; pending: bool                # an approval is waiting
    last_seq: Seq                                     # the change sequence of the last change to this session

class SpecRef(BaseModel):
    id: str; version: str | None = None; digest: str | None = None  # missing imported metadata stays unknown
class ToolInfo(BaseModel):
    name: str; version: str | None = None; tool_digest: str | None = None
    description: str | None = None

class SessionView(BaseModel):
    summary: SessionSummary; settings: dict[str, JsonValue]
    specs: dict[str, SpecRef]                         # the specs the session has run: id, version, digest
    pending: PendingConfirmation | None; tools: tuple[ToolInfo, ...]
    tools_status: Literal["not_loaded", "ready", "unsupported"] = "not_loaded"

class CallManifest(BaseModel):
    position: int; call_id: str; tool: str; args_digest: str; tool_digest: str
    effect: Literal["read_only", "effectful"]; replay: Literal["safe", "unsafe"]
class HistorySlot(BaseModel): entry_index: int; value: HistoryValue
class HistoryPage(BaseModel): items: tuple[HistorySlot, ...]; next_start: int | None

class SessionSnapshot(BaseModel):
    view: SessionView; through_index: int; seq: Seq    # -1 when no entries; one atomic owner-scoped read

class EffectInfo(BaseModel):
    invocation_id: str; tool: str; intent_index: int; reason: str

class SessionQuery(BaseModel):
    owner: str                                        # required
    ref: str | None = None
    status: frozenset[str] | None = None; tags_all: tuple[str, ...] = ()
    updated_after: datetime | None = None; updated_before: datetime | None = None
    text: str | None = None                            # authorized title/metadata; MAY decline with Unsupported
    order: Literal["updated_desc", "created_desc", "created_asc"] = "updated_desc"

class Page(BaseModel, Generic[T]):  items: tuple[T, ...]; next: Cursor | None
class EntryPage(BaseModel):  items: tuple[Entry, ...]; next_start: int | None; partial: bool = False
class EffectPage(BaseModel): items: tuple[EffectInfo, ...]; next_start: int | None
```

| Rule | Requirement |
|------|-------------|
| Projections | A summary is a projection of the log. The store MUST update it in the same transaction as the append that changes it, and a conformance test MUST be able to rebuild it from the log and find it equal. |
| Cheap lists | A list MUST NOT read entries or hydrate external history and MUST NOT compute a total. |
| Preview/search authority | Previews are disabled by default. An explicitly configured preview may contain only bounded content independently authorized as session metadata under current READ_SESSION policy; it cannot inherit authority merely from a past history read. Externally permissioned payloads and mixed-source derivatives MUST NOT be copied into list previews, search indexes, tags, titles or feed summaries by the engine unless independent metadata disclosure authority is explicit. A history access denial/revocation cannot be bypassed through those projections. Content search requiring provider authorization uses a separately qualified source path or is refused; cheap metadata search never queries or exposes a restricted cached snippet. |
| Usage completeness | Aggregate known attempt usage once by attempt identity, including failed attempts when reported. A missing count is not zero; usage_complete remains false while any attempt lacks authoritative usage. Cache/reasoning subcounts are not added to provider totals again. |
| Keyset paging | A page's `next` is an opaque keyset cursor over the query's order; a store MUST NOT page by offset. A cursor from a different query or a different tier is refused. |
| Entry paging | `entries` addresses by position: items from `start` (inclusive), in order or reversed, at most `limit`; `next_start` is the next position or `None`. |
| Owner scope | Every operation takes the owner and MUST see only that owner's sessions. |

### 3. The live store port

```python
class StoreCapabilities(BaseModel):
    durability: Literal["none", "process", "os"]      # what survives when an append has returned
    lease: Literal["process", "cross_process"]        # how far a turn lease reaches
    fencing: bool                                     # the commit checks the lease token in the same transaction
    notifications: bool = False                       # pushes changes to followers in other processes (otherwise they poll)
    # always true, so not declared: atomic append, idempotent keys

class SessionInit(BaseModel):  ref: str | None = None; spec: str | None = None; title: str | None = None; tags: tuple[str, ...] = (); settings: dict[str, JsonValue] = {}
class SessionPatch(BaseModel):
    title: str | None = None
    tags: tuple[str, ...] | None = None
    settings: dict[str, JsonValue] | None = None
# Omitted patch fields do not change state. Explicit title=None clears a title at the
# store boundary. tags/settings=None do not change those fields; an empty tags tuple
# clears tags, while supplied settings merge into existing settings after validation.

class LiveStore(Protocol):
    capabilities: StoreCapabilities
    # reads (the client API's backing)
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int) -> Page[SessionSummary]: ...
    async def entries(self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool) -> EntryPage: ...
    def changes(self, owner: str, *, session_id: str | None, since: Seq | None) -> AsyncIterator[PersistedChange]: ...
    async def snapshot(self, session_id: str, owner: str) -> SessionSnapshot: ...
    async def effects(self, session_id: str, owner: str, *, start: int, limit: int) -> EffectPage: ...
    async def find(self, owner: str, ref: str) -> SessionSummary | None: ...
    async def lookup(self, session_id: str, owner: str, *,
                     kind: Literal["key", "invocation", "model_call", "turn"], value: str,
                     limit: int = 1000, max_bytes: int | None = None) -> tuple[Entry, ...]: ...
    async def aclose(self) -> bool: ...              # seals the store; false means actual I/O remains
    # writes
    async def create(self, owner: str, init: SessionInit) -> SessionSummary: ...      # idempotent on (owner, init.ref)
    def turn(self, session_id: str, owner: str, *, turn_id: str) -> AbstractAsyncContextManager[TurnLog]: ...
    async def update(self, session_id: str, owner: str, patch: SessionPatch) -> SessionSummary: ...
    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]: ...

class TurnLog(Protocol):                              # the write handle for one turn; holds the session lease
    session: SessionState                             # a snapshot of the log at the lease (CONTRACT-002)
    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]: ...    # one atomic commit
    async def heartbeat(self) -> None: ...

class RecoveredTurn(BaseModel):  session_id: str; turn_id: str; resumable: bool
```

| Rule | Requirement |
|------|-------------|
| Atomic commit | `append` MUST write its entries, the updated summary, and the change records in one atomic commit, and MUST return only after the commit has the durability the store declares. |
| Durability is declared, not assumed | A store MUST NOT declare a durability it does not provide. `none` means the commit is lost with the process; `process` means it survives a process kill; `os` means it survives an operating-system crash. |
| Lease | `turn` takes a per-session lease for the block, as far as `capabilities.lease` says, and raises `TurnInProgress` when it is held. The lease expires when its heartbeat is older than the store's lease window. It is released when the block exits for any reason. |
| Fencing | A store that declares `fencing` MUST check the lease token in the same transaction as each append, so a stale runner cannot append after recovery. |
| Lost lease | After a lease expires, an `append` MUST raise `LeaseLost` and write nothing. |
| Recovery | `recover` finds sessions whose lease expired with a turn open and appends a `turn_ended` entry with stop reason `interrupted` for each, in one commit, and returns what it found. It is idempotent. |
| References | `create` with a `ref` that exists for the owner returns the existing session and writes nothing. A `ref` is unique per owner. |
| Ownership | An owner that does not own the session, and an id that does not exist, MUST both raise `UnknownSession` with no distinguishing detail. |
| Closed | A closed session accepts no turn and `turn` raises `SessionClosed`. |
| Change records | Every commit that changes a summary writes a change record with the next per-owner `Seq`. The store retains change records for a configured window. |
| Tool view readiness | An empty tools tuple with tools_status="not_loaded" does not assert that the selected specification has no tools. Current provider/spec capability rendering must be qualified before reporting ready; the UI never derives execution authority from this view. Pending metadata and normalized arguments use the same typed PendingConfirmation shape, with arguments=None until verified hydration. |
| Snapshot | `snapshot` reads view, pending approval, inclusive entry head and per-owner sequence atomically. Pending metadata and the immutable argument-source ref are maintained transactionally. Snapshot does not read an external history source and returns arguments as `None` with `history_status="not_loaded"`, which is distinct from a source read returning pending. `Client.pending` performs the separate bounded authorized hydration; it never scans the transcript or treats unavailable arguments as approved empty input. |
| Indexed lookup | `lookup` resolves only accepted keys, invocation manifests/facts, logical model calls/attempts, or turn ids within the selected owner/session. It preserves commit order, refuses count/byte overflow before copying a result, and never treats an input provider id/ref as authority. Indexes rebuild from accepted facts. It returns metadata entries, never hydrates source records. A missing group returns an empty tuple; the Client maps an unknown logical call to UnknownModelCall. |
| Effect projection | Started effectful invocations lacking a proved final outcome/resolution remain in an indexed unresolved-effect projection, including an `interrupted/outcome_unknown` result. `effects` pages by intent position; it never scans the transcript. Resolution updates this projection, count, summary and changes in one fenced commit. |
| Approvals wait without a lease | A session waiting for an approval holds no lease, so it survives a restart; an answer takes the lease like any turn. |

**Recovery guarantees, by declared capability.** These are the only guarantees; a store that does not meet a row's condition does not get its guarantee.

| Store (declared) | What holds | Not guaranteed |
|------------------|-----------|----------------|
| `durability=none`, `lease=process` (the in-memory store) | Atomicity and ordering within one process; recovery of a failed turn task in a living process | Anything across a process death. "A killed turn resumes" does not apply. |
| `durability=process` or `os`, `fencing=true` | Committed steps survive; an intent entry is present before every effectful tool runs; a stale runner cannot append; recovery marks a dead turn interrupted | Exactly-once external effects (ADR-014) |
| `durability=process` only | Survives a process kill | A commit just before an operating-system crash can be lost; an effect may then be unrecorded and its intent absent, so "not started" may be wrong |
| `fencing=false` | Single-runner behavior if the application guarantees one runner | Protection from a stale runner appending after recovery; a double execution is possible |
| `lease=process` with several processes | Nothing across processes | One turn per session across processes; use a `cross_process` store |

A `Tether` configured to resume (`resume` other than `off`) MUST refuse a store that declares `durability=none` or `fencing=false`, and MUST validate the chosen writable history provider's recovery durability/availability profile with `UnsupportedStore`. The initial local composition accepts only immediate writable history covering message, summary and model_exchange, with durability at least that of the journal; external/eventual recovery needs an explicitly qualified conditional profile and is refused until supplied.

### Local JSONL execution profile

`JsonlSessionStore(root, durability, limits)` implements LiveStore separately from `JsonlHistory`. A shared root-owner service grants one operating-system-owned writer for both adapters; independent processes opening the same root are refused. The local profile has process-scoped leases with owner-token fencing inside the writer. Shared/network filesystems and multi-host writers are unsupported; Postgres supplies the cross-process profile.

One journal commit is one versioned UTF-8 JSONL batch containing its entry payloads, summary/index updates, change records, idempotency keys, owner sequence and prior-batch digest. A complete checksummed batch is the recovery unit; the writer appends and acknowledges only at the configured fsync boundary, then publishes indexes/events. A torn final uncommitted batch is isolated and reported; corruption inside an acknowledged/history-referenced prefix is an integrity failure, never silently skipped. Lost/rebuilt sidecar indexes cannot change accepted order, keys or digests.

Snapshots/lists/pages use materialized indexes in memory; open/recovery rebuilds them by bounded-memory streaming. Per-session leases and heartbeats carry a generation token checked by the writer before each batch; local late workers cannot append after recovery. `durability=process` or `os` is claimed only for a qualified file/fsync profile, never merely because a file exists. Limits bound batch bytes, startup work reporting, queued I/O and offload capacity. This profile is specified, not passing evidence.

If a write is proven not committed it raises `StoreError`. If acknowledgment/fsync fails and commitment cannot be proved either way, it raises `CommitUnknown`, stops dependent dispatch and reports explicit uncertainty. Recovery resolves by the original idempotency key and valid batch prefix, never by blindly repeating an external action. Content-before-journal-ref ordering follows CONTRACT-005; no file/database distributed-transaction guarantee is made.

### 4. The archive ports

```python
class ArchiveCapabilities(BaseModel):
    kind: Literal["authoritative", "derived"]
    writable: bool                                    # has a sink half
    lossless: bool                                    # holds every entry kind
    restorable: bool                                  # can restore a session to the live store
    call_detail: bool                                 # optional legacy provider-id import, not the live Client lookup
    content: Literal["full", "facts_only"]

class ArchivedSession(BaseModel):  summary: SessionSummary; view: SessionView; entries: tuple[Entry, ...]

class ArchiveSink(Protocol):
    capabilities: ArchiveCapabilities
    async def archive(self, session: ArchivedSession) -> None: ...                  # idempotent by (session id, entry_count)

class ArchiveSource(Protocol):
    capabilities: ArchiveCapabilities
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int, actor: Actor) -> Page[SessionSummary]: ...
    async def get(self, session_id: str, *, actor: Actor) -> SessionView | None: ...
    async def entries(self, session_id: str, *, actor: Actor, start: int, limit: int, reverse: bool) -> EntryPage: ...
    async def call_detail(self, request_ids: Sequence[str], *, actor: Actor) -> Mapping[str, LegacyCallDetail | Pending | Denied | Unavailable]: ...
    async def restore(self, session_id: str, *, actor: Actor) -> ArchivedSession: ...   # only if `restorable`

class LegacyCallDetail(BaseModel): request_id: str; request: JsonValue | None; response: JsonValue | None
class CallDetail(BaseModel):
    identity: AttemptIdentity
    receipt: ResponseReceipt | None                 # latest observed receipt, if any
    receipts: tuple[ResponseReceipt, ...] = ()        # every committed receipt in observation order
    status: HistoryStatus
    observations: tuple[HistoryValue, ...]            # all relevant invocation/component refs, never first-row wins
    reason: str | None = None
```

| Rule | Requirement |
|------|-------------|
| Optional | A deployment without an archive works. No turn, list of live sessions, or recovery depends on one. |
| History is separate | An external history provider may be required to hydrate source text or tool arguments, with a bounded explicit failure. That dependency is not an archive read, cannot be hidden by empty payloads, and never changes execution facts. `call_detail` resolves known model-call refs through HistoryProvider; legacy derived-archive detail is optional historical import only. |
| Never in the turn's path | The engine calls `ArchiveSink.archive` only after a session is closed or idle by the application's policy, never inside a turn, and a failure is audited and never raised into a turn. |
| Authoritative | An authoritative archive written by tether MUST hold every journal entry unchanged and declare whether it also preserves all referenced history records. `lossless/restorable` require closure over those refs or a verified durable binding; metadata-only export cannot claim complete offline history. |
| Derived | A derived archive reconstructs what an external log holds (content and usage). It MUST declare `lossless=false` and `restorable=false`, MUST mark every summary and entry page it returns `partial`, and MUST NOT present tool executions, approvals, or intents it does not have. |
| As the caller | Every read takes the calling actor and uses its credential (ADR-011). A read is bounded by a time limit. |
| Not silently empty | A read that is not finished is reported `Pending`, one the caller may not make `Denied`, and one that failed `Unavailable`. It is never an empty result. |
| Read-only unless restored | An archived session cannot take a turn. `restore` is offered only by a `restorable` archive and creates the session in the live store with its entries, in one atomic commit, under the same `ref`. |
| Tiers | A summary carries `tier`. A cursor belongs to its tier. The API promises no merged ordering across tiers. |

### 5. Changes and the change feed

```python
class PersistedChange(BaseModel):  seq: Seq; session_id: str; kind: Literal["updated", "removed"]; summary: SessionSummary | None; entries: tuple[Entry, ...]
# the feed a client sees:
class SessionUpdated(BaseModel):  seq: Seq; summary: SessionSummary; last_in_commit: bool
class SessionRemoved(BaseModel):  seq: Seq; session_id: str; last_in_commit: bool
class EntriesAppended(BaseModel):  seq: Seq; session_id: str; entries: tuple[Entry, ...]; last_in_commit: bool
class ReplyText(BaseModel):
    session_id: str; turn_id: str; attempt_id: str; offset: int; text: str  # Unicode character offset, live only
class ReplyReset(BaseModel):
    session_id: str; turn_id: str; attempt_id: str; reason: Literal["outage", "cancelled", "reconnect"]
class Lagged(BaseModel):  missed: int                                           # this follower fell behind and lost live events
class ResyncRequired(BaseModel):  reason: Literal["too_old", "gap"]
Change = SessionUpdated | SessionRemoved | EntriesAppended | ReplyText | ReplyReset | Lagged | ResyncRequired
```

| Rule | Requirement |
|------|-------------|
| Resume | `follow(since=N)` yields persisted changes with `seq > N` in order, then continues live. `since=None` means from now, with no replay. If `N` is older than the retained window, the feed yields `ResyncRequired("too_old")` first and then continues live; the follower lists again. |
| Persisted versus live | `SessionUpdated`, `SessionRemoved`, and `EntriesAppended` carry a `seq` and are authoritative. `ReplyText` is a live convenience: a follower that misses it loses nothing, because the finished message arrives as an entry. |
| Provisional text | `ReplyText` belongs to one attempt, starts at offset 0 and advances by character count. Coalescing is permitted only for contiguous offsets of the same attempt. On outage/cancellation the engine emits `ReplyReset`; a client discards that attempt. Reconnect, a gap or `Lagged` also invalidates provisional text even if its reset was missed. Persisted terminal messages/status replace provisional output and are authoritative; missing deltas are not replayable from an unbounded memory buffer. |
| Coalescing | When a follower's queue backs up, adjacent contiguous `ReplyText` events of one attempt MUST be merged before any event is dropped. Persisted changes are never dropped from a retained window; a follower too slow to keep up receives `Lagged` and, if its position leaves the window, `ResyncRequired`. |
| Backpressure | Each follower has a bounded queue. A slow follower MUST NOT slow the engine or other followers. |
| Order | Within a session, changes arrive in log order. |
| Scope | A feed is scoped to an owner, and optionally to one session. |
| Visibility | Persistence is never deferred. A *visibility* policy that shows a turn's entries to readers only when the turn ends is reserved: it would be a per-session watermark in the read path (`entries`, `follow`, and the summary), advanced at the turn's end, including an end that recovery appends. It is not offered in v1, and no store may hold entries back from durable commit to implement it. |

### 6. The client API

```python
class Client(Protocol):                               # obtained as `tether.client(actor)`; every call is scoped to the actor's id as owner
    async def list(self, query: SessionQuery | None = None, *, tier: Literal["live", "archive"] = "live",
                   cursor: Cursor | None = None, limit: int = 50) -> Page[SessionSummary]: ...
    async def get(self, session_id: str, *, tier: Literal["live", "archive"] | None = None) -> SessionView: ...
    async def entries(self, session_id: str, *, tier: Literal["live", "archive"] | None = None,
                      start: int = 0, limit: int = 100, reverse: bool = False) -> EntryPage: ...
    async def history(self, session_id: str, *, start: int = 0, limit: int = 100) -> HistoryPage: ...
    async def pending(self, session_id: str) -> PendingConfirmation | None: ...
    async def snapshot(self, session_id: str) -> SessionSnapshot: ...
    async def effects(self, session_id: str, *, start: int = 0, limit: int = 50) -> EffectPage: ...
    async def call_detail(self, session_id: str, model_call_ids: Sequence[str]) -> tuple[CallDetail, ...]: ...
    def follow(self, *, session_id: str | None = None, since: Seq | None = None) -> AsyncIterator[Change]: ...
    async def find(self, ref: str) -> SessionSummary | None: ...
    async def create(self, *, ref: str | None = None, spec: str | None = None, title: str | None = None,
                     tags: Sequence[str] = (), settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def send(self, session_id: str, text: str, *, request_id: str | None = None,
                   attachments: Sequence[AttachmentRef] = (), spec: str | None = None) -> TurnHandle: ...
    async def confirm(self, session_id: str, invocation_id: str, answer: Answer) -> TurnHandle | None: ...
    async def resume(self, session_id: str) -> TurnHandle: ...
    async def resolve(self, session_id: str, invocation_id: str, outcome: Literal["completed", "not_completed"], *, reason: str) -> SessionSummary: ...
    async def cancel(self, session_id: str) -> bool: ...
    async def update(self, session_id: str, *, title: str | None = None, tags: Sequence[str] | None = None,
                     settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def close(self, session_id: str) -> SessionSummary: ...
    async def restore(self, session_id: str) -> SessionSummary: ...                  # only from a restorable archive

class TurnHandle(Protocol):
    turn_id: str
    async def result(self) -> TurnResult: ...
    async def cancel(self) -> None: ...

class Tether:                                          # the composition: engine, live store, optional archive, change bus
    # Constructed with engine, live store, history provider, and optional archive.
    def client(self, actor: Actor) -> Client: ...
    async def __aenter__(self) -> "Tether": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

| Rule | Requirement |
|------|-------------|
| History | `history` hydrates only referenced content in a bounded position page; every slot reports readiness independently. Entries/snapshots/listing remain usable during provider outage and carry refs/facts, not empty substituted text. `pending` first verifies authorized raw source arguments against CallManifest.args_digest, then validates with the pinned tool/schema and displays the normalized executable values whose digest the approval binds. `confirm` repeats both checks and cannot dispatch changed executable values under an earlier approval. The selected HistoryProvider is a separate Tether composition dependency, defaulting to JSONL in reference clients. |
| One object | A UI needs only `Client`. It never imports a store or the engine. |
| Payload disclosure | `call_detail` payloads and exports containing model exchanges additionally require current `READ_CALL_PAYLOAD` for the session. READ_SESSION alone permits metadata and separately authorized original-message history, not raw exchange bodies. Denial returns no exchange payload; it never falls back to cached or broader-identity content. Engine use of original sources is governed by READ_SESSION and CALL_MODEL, not a UI payload grant. |
| Identity | Each read/snapshot/effects operation and each feed delivery/replay batch is authorized with `READ_SESSION` for the bound actor and owner; denial or policy failure stops it without revealing content. `client(actor)` binds an actor. Commands that start model calls (`send`, `confirm`, `resume`) use its credential for that call and never persist it (ADR-011). A metadata-only follower needs no model credential; hydrating external source content may require its own current caller credential. |
| Send | `send` durably writes the user content through HistoryProvider, then commits its ref and the turn start, then returns a `TurnHandle` without waiting for the turn to finish. The turn runs as a task owned by `Tether`; the consumer follows it through `follow` or awaits `result`. |
| Idempotent send | `send` with a `request_id` that exists in the session with the same canonical accepted input (text, attachments and requested spec) digest returns the existing turn's handle; a different digest raises `RequestConflict`. The key is `request:<request_id>` (ADR-018). |
| One turn | `send` to a session with a running turn raises `TurnInProgress`; the running turn is unaffected. |
| Supersession | `send` to a session with a pending approval supersedes it: each pending call gets a denied result with reason `superseded`, then the new turn starts (ADR-015). |
| Reconnect | A client clears provisional output, calls `snapshot`, reads entries only through its returned head, and starts `follow(since=snapshot.seq)`. Replayed changes beyond that head are merged by entry index; `seq` is an atomic commit watermark, not a unique event id, so all variants of one commit are consumed before advancing it at `last_in_commit=true`. Exactly one last marker closes each nonempty filtered commit, and replay repeats the whole commit if the previous connection stopped before that marker. If retention is lost, abandon the bootstrap and obtain a new snapshot. Pending approvals come from the snapshot plus subsequent persisted changes; a stream delta never recreates or approves them. |
| Confirm | `confirm` answers the pending approval named by `invocation_id`. It takes the lease, checks the answer's key, and resumes the turn as a new task. It returns `None`, and changes nothing, when `invocation_id` is not pending or another answer already won. |
| Resume | `resume` continues the session's interrupted turn as a new turn (ADR-010). It raises `NotInterrupted` when there is none, `ResumeLimit` when the chain limit is reached, and `UnsupportedStore` as in section 3. |
| Cancel | cancel cancels the session's running turn and returns whether there was one. Steps already committed remain. A cancel on a pending-approval turn returns false; it does not count as a declined approval. Use confirm(declined), a superseding send, or close to settle waiting work. |
| Close | close is owner-authorized and lease-serialized with send/confirm/resolve/update. An active turn refuses with TurnInProgress; a real outstanding worker refuses with WorkerOutstanding; an unresolved effect refuses with EffectsUnresolved so resolution remains possible. Otherwise close seals any incomplete non-effectful call groups, supersedes every pending approval with non-success results, and commits closure/projections/feed atomically. Content writes precede that commit; failure leaves the accepted session state unchanged or explicitly CommitUnknown. Repeating close returns the existing closed summary. A closed session remains readable but accepts no new turn, approval or settings mutation. Closing is not deletion, eviction, or proof that an external effect stopped. Races have one accepted ordering; a late confirm cannot revive closed work. |
| Observation receipts | CallDetail retains every committed receipt, including when history is unavailable/denied or two observations share payload bytes. An observation sourced from a receipt carries its zero-based `receipt_index` in HistoryValue.source_locator. Request-only preparation remains explicit; unobserved response content is not invented. Raw-payload denial retains authorized identity/receipt metadata and returns denied values without provider lookup. |
| Call detail | call_detail accepts only logical model_call_ids reachable from the selected owner's session journal. Unknown/unreachable ids refuse with UnknownModelCall before provider lookup. Return one CallDetail per prepared attempt in requested call order then committed attempt order, retaining every verified provider invocation observation and every HistoryStatus. Receipts and source locators preserve provider ids separately; input provider request ids, traces or arbitrary refs never grant access. A source hole never removes an accepted attempt from the result. Legacy ArchiveSource.call_detail is an explicitly separate historical-import surface and cannot implement this lookup. |
| Read limits | list/entries/history/effects/call_detail enforce positive finite page, id-count, decoded-byte and deadline caps from explicit client limits; oversized/invalid requests refuse before allocation/provider I/O. A cursor is owner/query/tier/version-bound and never an authorization grant. No error reason includes content. |
| Resolve | P0. `resolve` requires a current actor, `Policy.authorize(RESOLVE_EFFECT)`, the session lease and an unresolved invocation. A same-process running worker refuses it. Append `resolved` with its outcome/actor/reason and atomically clear the indexed barrier; identical retries return the existing summary, different outcomes/reasons raise `ResolutionConflict`. Already-known non-unknown invocations raise `NotUnresolved`. If the unknown invocation lacks a terminal tool-result message, resolution first appends its single interrupted/unknown result in the same transaction; an optional reconciler output is a labelled assistant resolution notice, not another tool result. Resolution creates no new execution and cannot change its original evidence. |
| Shutdown | Leaving `Tether`'s context cancels turns and waits for cleanup at most `shutdown_timeout_seconds`, then closes stores without promising running threads/effects stopped. Their actual lifetime retains executor permits, and late callbacks cannot write through a closed or stale log. No automatic replay is authorized by shutdown. |
| Settings | `update` records a `settings` or `session` entry and the change appears in the feed. |
| Defaults | A `SessionQuery` of `None` lists the owner's live sessions, most recently updated first. |
| Application references | A `ref` ties a session to an application's own record; tether never reads the application's records (ADR-018). |

**Collection authorization.** Client list/find authorize READ_SESSION on `owner:<actor.id>`
for the bound owner and on each returned session id. A denied row stops the operation without
returning a cursor or metadata from that page. Per-session reads use the requested session id.
All deliveries recheck current policy after source I/O; cached content is never a grant.

**Resource ownership.** Tether owns the supplied store lifetime once entered. Close seals
client operations and submissions immediately. If actual Engine work survives its shutdown
deadline, close returns incomplete and retains the store/root ownership in deferred cleanup
until that work exits. A canceled close also arranges cleanup; it never cancels the underlying
worker future or hands the namespace to another writer early. Externally owned provider pools
require their selected adapter's explicit ownership configuration.

**Event-loop ownership.** `Tether.__aenter__` binds composition, owned tasks and async
resources to the current running event loop. All client I/O and shutdown MUST run on that
loop; a wrong-loop call raises `RuntimeError("wrong_event_loop")` before touching resources.
An external thread bridge may submit to the bound loop; no blocking bridge may run on it.
Engine and storage lifetimes cannot migrate async pools/locks/tasks between loops.

**`Tether` options.** `history` is an explicit HistoryProvider; reference clients supply local JSONL and tests supply InMemoryHistory.  Executor limits and shutdown deadline are explicit finite settings (CONTRACT-002); `max_resolution_reason_chars` bounds operator text. `max_pending_argument_bytes` and `max_resolution_reason_chars` are positive finite limits; oversized inputs are refused before persistence. Read/argument/context limits are shared with CONTRACT-001/004/005. The demo uses `resume="off"` on the non-durable in-memory store.  `resume` (`"off"`, `"report"`, or `"auto"`; the durable reference default is `"report"`), `resume_actor` (a function that returns an actor with a live credential for a session, or `None`, used only with `"auto"`), `recover_interval`, `max_resumes` (the reference configuration proposes 3, qualified before release), `lease_window` and `heartbeat_interval`, `approval_ttl` (none by default), `archive` and `archive_on` (a policy for when a session is archived), and the archive read time limit. The numeric defaults other than those named are supplied by the owner after the first baseline.

### 7. Performance rules

| Rule | Requirement |
|------|-------------|
| List and get | Cost is independent of transcript length: they read summaries and the session's settings, never entries. |
| Entry pages | Cost is proportional to `limit`, not to the session's length. |
| Follow | Memory per follower is bounded by a configured queue size; an event carries a delta (an entry or a summary), never a whole transcript. |
| Read path | The store constructs models from its own output without re-validating them; validation happens at write. |
| No blocking | Every store call is asynchronous, or a blocking driver runs in a worker thread. |
| Archive reads | Asynchronous, time-limited, and never on a turn's path. |
| Budgets | The benchmark suite measures list, get, entry-page, append, and follow latency and memory, and fails on regression against a committed baseline. The proposed targets are in the PRD (NFR-7); the first baseline validates or replaces them. |

### 8. Dummies and conformance suites

The package ships `InMemoryStore` (a live store declaring `durability="none"`, `lease="process"`, `fencing=true`) and `InMemoryArchive` (authoritative, lossless, restorable). A local single-writer `JsonlSessionStore` and `JsonlHistory` pair is the reference default; both need their applicable file/restart suites. A Postgres live store ships in an extra (ADR-020); a SQLite store is optional. `tether.testing.conformance.live_store_suite(factory)` runs, as asynchronous tests, with each durable test run only for a store that declares the capability it needs: atomic commit (a proven rejected append writes nothing; an uncertain acknowledgment raises CommitUnknown and is recovered by the original key), summary equals the rebuilt projection, dense positions, idempotent keys, lease exclusivity across tasks and, for `cross_process` stores, processes, fencing (a stale append is refused), lost-lease refusal, idempotent recovery, owner scoping, `ref` uniqueness and idempotent create, keyset paging stability under concurrent writes, change-feed order, resume and resync, coalescing, backpressure isolation, that no list reads entries, and, for `durability` of `process` or `os`, that a committed append survives closing and reopening the store (a real-process kill test is part of the Postgres and SQLite suites). The live-store suite also proves snapshot/head/sequence atomicity against concurrent appends and approval answers, materialized pending metadata/source refs and bounded authorized argument hydration without transcript scans, indexed unresolved-effect paging/counts, idempotent/conflicting resolution, context-record losslessness, failed context commits, and same-commit feed variants without skipped entries. `archive_suite(factory)` runs: idempotent archive, authoritative losslessness, derived marking `partial`, read-as-caller, pending, denied, and unavailable reporting, and restore for a restorable archive. Operating-system-crash durability cannot be tested in continuous integration, and the guarantee table says so.

### Fork/reference isolation (P2)

Fork is a deferred capability, not a v1 client method. A future fork remains owner-scoped: it authorizes and materializes each required source component, creates destination-session refs through the selected writable provider, and retains provenance to the source. It cannot place another session's ref directly into the destination and bypass HistoryProvider session checks. Missing/denied/incompatible source aborts acceptance; orphan destination content is not a forked session. Copied calls, approvals and effects are historical evidence, never executable recovery work or transferable authorization. Incomplete groups require explicit non-executable closure at the fork boundary. New actions receive new invocation ids and fresh gates. Cross-owner sharing/import is outside this scope.

## Precedence and Compatibility

The pre-release v1 now includes atomic reconnect snapshots, unresolved-effect paging/resolution, context records and attempt-scoped live text. No released cursor/event/API compatibility is claimed or shimmed.

- Versioning: one integer, `SESSION_CONTRACT_VERSION`. A new entry kind is compatible, and a reader MUST ignore an entry kind it does not know.
- Ordering or precedence: this contract governs persistence and the client API; CONTRACT-002 governs which entries the engine writes and when, and its gate sequence.
- Backward-compatibility rules: adding an optional field, an entry kind, a capability, or a `Change` variant that a follower may ignore is compatible. Changing a signature, a rule, a guarantee, or a cursor format is a new version. A cursor format change invalidates outstanding cursors, which a store refuses with `InvalidCursor`.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| `UnknownSession` (missing or not the owner's) | Raised with no distinguishing detail | No | The caller answers as for a missing session |
| `WorkerOutstanding` | Same-process worker still alive; new turn/confirm/resume/resolve refused for that session | After worker exits | Never confuse cancelled wait with stopped effect |
| `NotUnresolved`, `ResolutionConflict` | Resolution target absent or duplicate disagrees | No | Read current effect state; do not overwrite a result |
| `EntryConflict` | Existing idempotency key has different payload | No | Refuse mutation; retain original record |
| `TurnInProgress` | Raised by `turn` and `send` | Yes, later | One turn per session at a time |
| `SessionClosed` | Raised by `turn` and `send` | No | Create a session |
| `LeaseLost` | Raised by `append`; nothing written | No | Recovery resolves the turn |
| Commit acknowledgment uncertain | CommitUnknown; dependent tool/model dispatch stops | No blind retry/dispatch | Recover the accepted batch/key before deciding whether work started |
| Close with unresolved effect | EffectsUnresolved; session stays open and resolvable | After authorized resolution | Never archive away the only recovery authority |
| Unknown logical model call | UnknownModelCall | No | No provider lookup or cross-session disclosure |
| `RequestConflict` | Raised by `send` | No | Use a new `request_id` |
| `NotInterrupted`, `ResumeLimit` | Raised by `resume` | No | The session has nothing to resume, or the chain limit stops a crash loop |
| `UnsupportedStore` | Raised at construction or by `resume` | No | Use a store that declares durable commits and fencing |
| `InvalidCursor` | Raised by `list` | No | List again from the first page |
| `Unsupported` | Raised for a query feature the store lacks (such as `text`) | No | Use a different filter |
| `NotRestorable` | Raised by `restore` | No | The archive is derived or not restorable |
| A follower falls behind | `Lagged`, then `ResyncRequired` if its position left the window | Yes | List again, then follow from the new position |
| Commit acknowledgment uncertain | `CommitUnknown`; no dependent tool/model dispatch | No blind retry | Recover the same batch/key and report uncertainty; do not assume no write |
| A store commit is proven rejected | The append raises `StoreError` and writes nothing | The engine does not retry a failed commit | The engine stops the turn; if the tool already ran, the invocation's outcome is unknown (ADR-014); `TurnHandle.result()` raises `StoreError`; if the store stays unavailable the lease expires and recovery marks the turn interrupted |
| The archive is slow or down | A read returns `Pending` or `Unavailable`; a sink failure is audited | The application's policy | No turn is affected |
| The process dies mid-turn | The lease expires; `recover` appends `turn_ended: interrupted` | n/a | The consumer sees the session as interrupted and a present caller resumes it |

## Examples

Reconnect with `snapshot = await client.snapshot(session_id)`, page entries up to `snapshot.through_index`, render pending metadata from `snapshot.view.pending` and hydrate its arguments through `client.pending`, then follow from `snapshot.seq`. Repeat this bootstrap on `ResyncRequired`. The client never interprets missing streamed text as a completed answer.

```python
async with Tether(engine=engine, store=InMemoryStore(), history=InMemoryHistory(), resume="off") as tether:
    client = tether.client(Actor(id="maya", credential=my_credential))
    session = await client.create(ref="crm-4411", title="demo")
    handle = await client.send(session.id, "look up the sample", request_id="r1")
    async for change in client.follow(session_id=session.id):
        if isinstance(change, ReplyText):
            print(change.text, end="")
        elif isinstance(change, SessionUpdated) and change.summary.status == "idle":
            break
    result = await handle.result()
    page = await client.list(SessionQuery(owner="maya", status=frozenset({"idle"})), limit=20)
```

```python
# After a crash: the session is interrupted; a present caller resumes it
summary = await client.find("crm-4411")
if summary and summary.resumable:
    handle = await client.resume(summary.id)
```

```python
# A gateway-backed deployment: live sessions, plus a derived archive for what the gateway logged
tether = Tether(engine=engine, store=PostgresStore(pool), history=GatewayHistory(reader=reader, supplement=local_history))   # the history provider ships with the Databricks profile
detail = await client.call_detail(session_id, [entry.model_call_id for entry in model_call_entries])
```

## Non-Normative Notes

- Position-addressed entries and keyset-addressed sessions are chosen so no read needs a scan. A UI that wants "jump to page N" of sessions must search or filter instead.
- A server adapter (HTTP, WebSocket, server-sent events) maps `Client` calls to routes and `follow` to a stream. Its authentication and the choice of the actor are its own.
- Preview and metadata search follow the explicit authority rule in section 2; a cached source read never grants later disclosure after source access is revoked.
- Execution facts, original conversation content and optional externally observed model exchanges have separate authorities. Gateway detail may enrich a separately retained original transcript; selecting that source does not imply that a deployment reconstructs its conversation from gateway logs.
- Original content is referenced through CONTRACT-005; the runtime journal keeps execution facts and ordering. Gateway reads are a history-source dependency only when content is requested, not an archive dependency of execution recovery.

## Open Items

| Item | Owner |
|------|-------|
| The retained window for change records, the default follower queue size, the lease window, the heartbeat interval, and the recovery interval | Erik LaBianca, from the first benchmarks |
| Whether `list` should accept an `as_of` position for stable paging under heavy writes beyond the keyset guarantee | Erik LaBianca |
| Fork and rewind (PRD FR-15) entry and API shape | Erik LaBianca, when FR-15 is scheduled |
| Gateway exact receipt/tag/row correlation and configured retention/coverage | Databricks provider qualification under CONTRACT-005 |
| The visibility watermark (section 5) | Erik LaBianca, when a consumer needs it |

Aggregate history/detail disclosure rechecks READ_CALL_PAYLOAD after assembly and final READ_SESSION authorization. A denial removes every raw exchange record already hydrated in that result, including earlier successful observations, while preserving identity/receipts and independently authorized original transcript records.
