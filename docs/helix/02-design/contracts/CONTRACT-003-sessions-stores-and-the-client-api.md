---
ddx:
  id: CONTRACT-003
  type: contract
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
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
#   turn_resumed     purpose: str; invocation_id: str | None; resumes: str | None; no extra turn count
#   turn_ended       stop_reason: StopReason; reason: str | None
#   model_call       tier: str; request_id: str | None; status: Literal["ok", "error", "incomplete"]; duration_ms: int
#                    model_call_id: str; attempt_ids: tuple[str, ...]; receipt: ResponseReceipt | None
#                    exchange_ref: HistoryRef | None
#                    purpose: Literal["answer", "summary", "correction"]; finish_reason: str | None
#                    preparation_ordinal/part_position/compaction_key: summary provenance under CONTRACT-004
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
#   context_preparation preparation: ContextPreparation # accepted ordinal/source/config/model-call binding, CONTRACT-004
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
    def for_actor(self, actor: Actor) -> LiveStore: ...  # pure non-owning current-caller facade
    workers: WorkerLifetime                     # concrete namespace-scoped process lifetime
    capabilities: StoreCapabilities
    # reads (the client API's backing)
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int) -> Page[SessionSummary]: ...
    async def entries(self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool) -> EntryPage: ...
    async def watermark(self, owner: str) -> Seq: ...       # atomic current owner commit position
    def changes(self, owner: str, *, session_id: str | None, since: Seq | None) -> ChangeFeed: ...  # async iterator with bounded aclose
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
    workers: WorkerLifetime                           # same handle as owning store/files
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

class ArchivedSession(BaseModel):
    view: SessionView                               # metadata projection, tier=archive
    bundle: ArchiveBundle                          # exact journal and reference-complete content
# The bundle is an authoritative transfer surface. Metadata/entry reads never return it.
# Derived sources have no restorable bundle and cannot satisfy ArchiveSink.

class ArchiveSink(Protocol):
    capabilities: ArchiveCapabilities
    async def archive(self, session: ArchivedSession) -> None: ...                  # idempotent by exact snapshot/bundle identity

class ArchiveSource(Protocol):
    capabilities: ArchiveCapabilities
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int, actor: Actor) -> Page[SessionSummary]: ...
    async def get(self, session_id: str, *, actor: Actor) -> SessionView | None: ...
    async def entries(self, session_id: str, *, actor: Actor, start: int, limit: int, reverse: bool) -> EntryPage: ...
    async def call_detail(self, request_ids: Sequence[str], *, actor: Actor) -> Mapping[str, LegacyCallDetail | Pending | Denied | Unavailable]: ...
    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]: ...
    async def restore(self, session_id: str, *, actor: Actor) -> ArchivedSession: ...   # only if `restorable`
# ArchiveReadError.status is pending, denied, or unavailable; get(None) means absent only.
# NotRestorable refuses derived/facts-only recovery; it never becomes a live empty session.

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
| Never in the turn's path | Application retention policy invokes an explicit bounded composition helper after idle/close, outside Agent turns. It exports a verified bundle and sends it to the configured sink. No automatic archive_on driver is selected; sink failure is explicit to that helper and cannot change a turn outcome. Archival never evicts live data. |
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
    async def history(self, session_id: str, *, tier: Literal["live", "archive"] = "live", start: int = 0, limit: int = 100) -> HistoryPage: ...
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
    async def update(self, session_id: str, *, title: str | Unset | None = UNSET, tags: Sequence[str] | None = None,
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
| Incomplete result | `TurnHandle.result()` reconstructs `model_incomplete` with the committed original text under the same bounded, current-authorized hydration path as final/approval results. Retained handles and request-id retries after reopen preserve its fixed finish reason, text and turn identity without a new model call. A denied read discloses no cached content; unavailable history remains explicit. Incomplete text carries no pending or executable tool-call authority. |
| Idempotent send | `send` with a `request_id` that exists in the session with the same canonical accepted input (text, attachments and requested spec) digest returns the existing turn's handle; a different digest raises `RequestConflict`. The key is `request:<request_id>` (ADR-018). |
| One turn | `send` to a session with a running turn raises `TurnInProgress`; the running turn is unaffected. |
| Supersession | `send` to a session with a pending approval supersedes it: each pending call gets a denied result with reason `superseded`, then the new turn starts (ADR-015). |
| Reconnect | A client clears provisional output, calls `snapshot`, reads entries only through its returned head, and starts `follow(since=snapshot.seq)`. Replayed changes beyond that head are merged by entry index; `seq` is an atomic commit watermark, not a unique event id, so all variants of one commit are consumed before advancing it at `last_in_commit=true`. Exactly one last marker closes each nonempty filtered commit, and replay repeats the whole commit if the previous connection stopped before that marker. If retention is lost, abandon the bootstrap and obtain a new snapshot. Pending approvals come from the snapshot plus subsequent persisted changes; a stream delta never recreates or approves them. |
| Confirm | `confirm` answers the pending approval named by `invocation_id`. It takes the lease and checks authoritative journal pending state before using any cached runner. The accepted confirmation and `turn_resumed` running projection commit atomically, without incrementing the logical turn count. Known acceptance precedes fallible continuation; the command returns a Tether-owned handle and current bindings/policy still govern dispatch. It returns `None` without recording an approval or starting a continuation when `invocation_id` is not pending or another answer already won. Store lease/recovery housekeeping retains its separate contract. |
| Resume | `resume` continues the session's interrupted turn as a new turn (ADR-010). It raises `NotInterrupted` when there is none, `ResumeLimit` when the chain limit is reached, and `UnsupportedStore` as in section 3. |
| Cancel | cancel cancels the session's running turn and returns whether there was one. Steps already committed remain. A cancel on a pending-approval turn returns false; it does not count as a declined approval. Use confirm(declined), a superseding send, or close to settle waiting work. Explicit cancellation projects idle/nonresumable, distinct from process/lease interruption under ADR-010. |
| Close | close is owner-authorized and lease-serialized with send/confirm/resolve/update. An active turn refuses with TurnInProgress; a real outstanding worker refuses with WorkerOutstanding; an unresolved effect refuses with EffectsUnresolved so resolution remains possible. Otherwise close seals any incomplete non-effectful call groups, supersedes every pending approval with non-success results, and commits closure/projections/feed atomically. Content writes precede that commit; failure leaves the accepted session state unchanged or explicitly CommitUnknown. Repeating close returns the existing closed summary. A closed session remains readable but accepts no new turn, approval or settings mutation. Closing is not deletion, eviction, or proof that an external effect stopped. Races have one accepted ordering; a late confirm cannot revive closed work. |
| Observation receipts | CallDetail retains every committed receipt, including when history is unavailable/denied or two observations share payload bytes. An observation sourced from a receipt carries its zero-based `receipt_index` in HistoryValue.source_locator. Request-only preparation remains explicit; unobserved response content is not invented. Raw-payload denial retains authorized identity/receipt metadata and returns denied values without provider lookup. |
| Call detail | call_detail accepts only logical model_call_ids reachable from the selected owner's session journal. Unknown/unreachable ids refuse with UnknownModelCall before provider lookup. Return one CallDetail per prepared attempt in requested call order then committed attempt order, retaining every verified provider invocation observation and every HistoryStatus. Receipts and source locators preserve provider ids separately; input provider request ids, traces or arbitrary refs never grant access. A source hole never removes an accepted attempt from the result. Legacy ArchiveSource.call_detail is an explicitly separate historical-import surface and cannot implement this lookup. |
| Read limits | list/entries/history/effects/call_detail enforce positive finite page, id-count, decoded-byte and deadline caps from explicit client limits; oversized/invalid requests refuse before allocation/provider I/O. A cursor is owner/query/tier/version-bound and never an authorization grant. No error reason includes content. |
| Resolve | P0. `resolve` requires a current actor, `Policy.authorize(RESOLVE_EFFECT)`, the session lease and an unresolved invocation. A same-process running worker refuses it. Append `resolved` with its outcome/actor/reason and atomically clear the indexed barrier; identical retries return the existing summary, different outcomes/reasons raise `ResolutionConflict`. Already-known non-unknown invocations raise `NotUnresolved`. If the unknown invocation lacks a terminal tool-result message, resolution first appends its single interrupted/unknown result in the same transaction; an optional reconciler output is a labelled assistant resolution notice, not another tool result. Resolution creates no new execution and cannot change its original evidence. Supplied operator reasons are nonempty and bounded (selected implementation: 1024 characters, plus the Client request-byte cap). Reused partial-group refs must match owner/session/message kind and call correlation; an existing source-less or contradictory immutable terminal entry refuses with an integrity reason and preserves the barrier. Identical retries recheck current authorization without appending another decision, including an already closed session. |
| Shutdown | Leaving `Tether`'s context cancels turns and waits for cleanup at most `shutdown_timeout_seconds`, then closes stores without promising running threads/effects stopped. Their actual lifetime retains executor permits, and late callbacks cannot write through a closed or stale log. No automatic replay is authorized by shutdown. |
| Settings | `update` records a `settings` or `session` entry and the change appears in the feed. Omitted title preserves its value; explicit `None` clears it. Omitted/None tags or settings preserve them; empty tags clear and supplied settings merge. Public create/update refuse engine-managed `held` settings before mutation; governed binders may journal authorized context changes through their separate extension seam. Generic metadata cannot manufacture scope authority. Commands copy and bound requests before policy awaits, require current owner/read authorization and recheck disclosure after the store returns. Store update owns the lease; the facade never nests a lease. A denied final disclosure does not undo a known committed mutation. |
| Defaults | A `SessionQuery` of `None` lists the owner's live sessions, most recently updated first. |
| Application references | A `ref` ties a session to an application's own record; tether never reads the application's records (ADR-018). |

**Approval annotations.** `Answer.note` is an informational caller annotation. Preserve a
supplied value, including an empty string, through a message-kind history reference on the
accepted confirmation with `note_kind="approval_note"`; never put its text in audit facts.
Prepare that content before the atomic decision/resume commit. A failed write leaves the
approval pending and cannot dispatch. The encoded note is bounded by the execution input
budget, in addition to the Client request-packet limit. It is outside executable arguments,
approval authority and automatic model input; an application/context strategy must explicitly
choose any later use. Hydration uses current caller READ_SESSION/source access, and archive
closure includes the reference. Retained successful handles wait for an accepted matching
continuation; a newer resumed phase without a terminal fact is `TurnInProgress`, never the
previous phase's waiting result.

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

**`Tether` options.** `history` is an explicit HistoryProvider; reference clients supply local JSONL and tests supply InMemoryHistory.  Executor limits and shutdown deadline are explicit finite settings (CONTRACT-002); `max_resolution_reason_chars` bounds operator text. `max_pending_argument_bytes` and `max_resolution_reason_chars` are positive finite limits; oversized inputs are refused before persistence. Read/argument/context limits are shared with CONTRACT-001/004/005. The demo uses `resume="off"` on the non-durable in-memory store.  `resume` (`"off"`, `"report"`, or `"auto"`; the durable reference default is `"report"`), `resume_actor` (a function that returns an actor with a live credential for a session, or `None`, used only with `"auto"`), `recover_interval`, `max_resumes` (the reference configuration proposes 3, qualified before release), `lease_window` and `heartbeat_interval`, `approval_ttl` (none by default), `archive: ArchiveSource | None` and `archive_sink: ArchiveSink | None` are independent optional halves. The explicit `Tether.archive_session(actor, session_id)` helper uses ClientLimits read/entry/byte bounds; application retention policy invokes it outside turns. Automatic archive_on scheduling is deferred, with no inert policy option. The numeric defaults other than those named are supplied by the owner after the first baseline.

### 7. Performance rules

| Rule | Requirement |
|------|-------------|
| List and get | Cost is independent of transcript length: list reads indexed summaries; get reads indexed summary/settings and bounded pending/source metadata. Neither scans the transcript or hydrates message content. |
| Entry pages | Cost is proportional to `limit`, not to the session's length. |
| Follow | Memory per follower is bounded by a configured queue size; an event carries a delta (an entry or a summary), never a whole transcript. |
| Read path | Validate writes and verify canonical bytes, integrity, scope and indexed consistency when decoding persisted data. Reuse already-verified immutable values where safe; never remove necessary read validation to meet a latency target. |
| No blocking | Every store call is asynchronous, or a blocking driver runs in a worker thread. |
| Archive reads | Asynchronous, time-limited, and never on a turn's path. |
| Budgets | The benchmark suite measures list, get, entry-page, append, and follow latency and memory, and fails on regression against a committed baseline. The proposed targets are in the PRD (NFR-7); the first baseline validates or replaces them. |

### 8. Dummies and conformance suites

The package ships `InMemoryStore` (a live store declaring `durability="none"`, `lease="process"`, `fencing=true`) and `InMemoryArchive` (authoritative, lossless, restorable). A local single-writer `JsonlSessionStore` and `JsonlHistory` pair is the reference default; both need their applicable file/restart suites. A Postgres live store ships in an extra (ADR-020); a SQLite store is optional. `tether.testing.conformance.live_store_suite(factory)` supplies reusable logical port checks. Complete profile conformance combines those checks with the adapter-specific qualifications named by its report; each durable qualification applies only when the store declares the capability it needs. Complete qualification MUST cover: atomic commit (a proven rejected append writes nothing; an uncertain acknowledgment raises CommitUnknown and is recovered by the original key), summary equals the rebuilt projection, dense positions, idempotent keys, lease exclusivity across tasks and, for `cross_process` stores, processes, fencing (a stale append is refused), lost-lease refusal, idempotent recovery, owner scoping, `ref` uniqueness and idempotent create, keyset paging stability under concurrent writes, change-feed order, resume and resync, coalescing, backpressure isolation, that no list reads entries, and, for `durability` of `process` or `os`, that a committed append survives closing and reopening the store (a real-process kill test is part of the Postgres and SQLite suites). Complete qualification MUST also prove snapshot/head/sequence atomicity against concurrent appends and approval answers, materialized pending metadata/source refs and bounded authorized argument hydration without transcript scans, indexed unresolved-effect paging/counts, idempotent/conflicting resolution, context-record losslessness, failed context commits, and same-commit feed variants without skipped entries. `archive_suite(factory)` supplies shared archive checks; complete archive qualification MUST cover: idempotent archive, authoritative losslessness, derived marking `partial`, read-as-caller, pending, denied, and unavailable reporting, and restore for a restorable archive. Operating-system-crash durability cannot be tested in continuous integration, and the guarantee table says so.

### Fork/reference isolation (P2)

Fork is a deferred capability, not a v1 client method. A future fork remains owner-scoped: it authorizes and materializes each required source component, creates destination-session refs through the selected writable provider, and retains provenance to the source. It cannot place another session's ref directly into the destination and bypass HistoryProvider session checks. Missing/denied/incompatible source aborts acceptance; orphan destination content is not a forked session. Copied calls, approvals and effects are historical evidence, never executable recovery work or transferable authorization. Incomplete groups require explicit non-executable closure at the fork boundary. New actions receive new invocation ids and fresh gates. Cross-owner sharing/import is outside this scope.

## Proposed next consumer command and transaction profile

This is the proposed post-0.1 contract revision for the immediately next consumer slice. The current Client and store interfaces above remain the implemented surface until Erik LaBianca accepts these paragraphs and their dependent design. First consumer adoption follows this slice; 0.1 qualification is unchanged.

### Client-origin invocation and preparation

The proposed `Client.invoke_tool` command takes `session_id`, `tool_name`, canonical JSON arguments and a required `request_id`, plus an optional requested specification. It returns a task-owned operation handle whose result is derived from accepted journal facts. Its fingerprint includes owner, command origin, tool/arguments and requested specification; equal retries share the accepted operation and conflicting retries refuse. Canonical argument content is accepted through HistoryProvider and bound by a verified ref/digest in the command journal entry. `origin="client"` and the accepted command-entry identity are persisted independently of model-call provenance. A retry retains that operation's pinned definition/generation rather than rebinding it to a replacement tool.

The command acquires the ordinary session lease, honors active-turn/actual-worker/unknown-effect/closed-session refusals and executes CONTRACT-002's full tool pipeline. Approval-required calls refuse before dispatch, and no model request, assistant-source entry or model-call row is synthesized.

The proposed leased-preparation participant attaches to accepted `send` input after idempotent admission. It receives the immutable accepted request identity and current actor under the same lease, returns ready/needs-input/refused, and may attach separately authorized preparation references without replacing text or attachment identity. Cancellation, deadline, source denial or lease loss ends preparation explicitly. Reopening an uncertain acknowledgment resolves by the accepted request/preparation key before another attempt. Client snapshots expose the waiting/refused state through committed metadata rather than claiming a model has started.

Acceptance MUST race command/send/close/confirm and competing processes; verify zero model calls for direct invocation; refuse approval-required calls; and crash at admission, preparation acceptance, intent, observable effect and result acknowledgment. Current caller authorization and bounded metadata/payload disclosure apply to every read of the operation.

### Settlement and state publication

Session storage MUST retain staged participant ids, the selected transaction profile, accepted settlement decision and known/uncertain outcome with the invocation's immutable bindings. The decision, result state and change-feed visibility MUST agree at one journal frontier. A shared-database participant has no independently published success before that frontier. An independent participant remains pending/uncertain until its same-id outcome is known; lease release or a new Client command cannot erase its barrier. Rollback failure is explicit uncertainty, not an empty result. Application code owns business payloads and reconciliation; the store exposes no raw connection through generic ToolContext.

Acceptance MUST cover guard withholding, participant failure, lost commit/rollback acknowledgment, duplicate resolution, actor revocation and process death at every decision/effect/result frontier described by CONTRACT-002. Snapshots, effects, entries and the feed MUST report the same accepted state after reopening.

### Specification, registry and edited approvals

An accepted transition records old/new specification id, version and digest plus the registry generation under the session lease, only between completed call groups. Outstanding incompatible calls refuse or close through their existing outcomes; unresolved effects persist. Registry publication is atomic and active invocations retain their pinned generation until actual exit. Pending inspection and confirmation MUST compare the present tool/specification/generation bindings and invalidate incompatible approvals. An edited confirmation preserves separate proposal, shown and executable digests and repeats source/schema/current-policy gates before intent; an existing intent forbids further editing.

Acceptance MUST race transition/publication with pending reads, answers, tool execution, close and recovery. A lost acknowledgment is recovered by its stable command key. No snapshot may advertise a new binding while its journal still authorizes the old one, and no stale answer may execute a changed definition or argument set.

### Auxiliary operations

An application-scheduled auxiliary model operation requires a current Actor, configured purpose/profile and source frontier. It acquires its own session operation/lease after the answer turn ends, records attempts and purpose-specific usage, and refreshes that caller's credential per physical attempt. Missing credentials, read/model denial or source drift refuse without application-identity fallback. The Client exposes accepted operation/result metadata through existing bounded reads; applying a suggested title or other application field remains an explicit authorized command. Core does not own a background scheduler or mandatory suggestion payload.

Acceptance MUST cancel queued and active work, revoke the caller between scheduling and egress/retry, race close or a new turn, and reopen missing/partial usage. Results and operational diagnostics preserve the same owner/payload boundaries as answer calls.

### Shared PostgreSQL history and journal transaction

When PostgresSessionStore and PostgresHistory share one verified root/namespace, their proposed default paired profile MUST commit new message content and its journal reference in the same lease-fenced database transaction. The internal adapter participant carries the present actor, immutable content digest/key, expected journal frontier and lease holder/generation. It is not a raw connection in ToolContext. The atomic operation key binds the complete content/reference batch: an equal retry returns its accepted references/frontier; conflicting content or bindings refuse. The committed change feed and summary advance with that same transaction. An uncertain acknowledgment is recovered by key under current caller authorization before another write is attempted.

A requested shared profile MUST refuse a mismatched root, unsupported participant or expired lease; it cannot silently fall back to separate commits. Independent roots/backends retain the explicit content-before-reference profile. Standalone history writes do not acquire journal atomicity by proximity. CONTRACT-005 defines the failure table and content-closure checks.

Acceptance MUST use separate processes/connections for transaction rollback after content staging, lease expiry, cancellation, commit acknowledgment loss, equal/conflicting retries, caller revocation and reopening. After every fault, either the complete content/reference/journal/change batch exists or none of it exists; unknown acknowledgment remains unknown until the key lookup proves its outcome.

### Defaults and configuration compatibility

The current release requires explicit ClientLimits, ModelExecutionLimits and per-tier model limits. The next compatibility slice proposes documented constructor/factory defaults derived from the already exercised reference-client values, with explicit owner overrides and a deterministic estimator/Full or Tail composition. It MUST NOT infer a provider context/output capacity, silently choose a live model profile or label a fabricated estimator as server-qualified. The exact default values and strategy selection require an accepted profile table before implementation; no new numeric budget is established by this paragraph.

Acceptance MUST show the documented minimal composition, every override, deterministic repeated estimation, explicit unknown model capacity refusal, and the same finite request/worker/read bounds under the selected defaults. Changes to published defaults are versioned compatibility changes.

## Precedence and Compatibility

The pre-release v1 now includes atomic reconnect snapshots, unresolved-effect paging/resolution, context records and attempt-scoped live text. No released cursor/event/API compatibility is claimed or shimmed.

- Versioning: one integer, `SESSION_CONTRACT_VERSION=4`. Version 4 requires pure for_actor(actor) binding for every caller path. Version 3 requires a namespace-scoped `WorkerLifetime` on SessionStore, TurnLog and JournalFiles; custom adapters must provide it and retain underlying resources through actual worker exit. A new entry kind is compatible, and a reader MUST ignore an entry kind it does not know.
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

The read-side replay worker captures an atomic owner watermark before starting from now or
resetting a retained-window gap. Each follower has an independent finite queue; backpressure
pauses only that replay worker. Persisted records remain in the store's retained window. A
queue-full diagnostic `Lagged(missed=0)` means the reader paused without losing live text;
actual lost provisional deltas require a positive count. A retained-window gap yields
`ResyncRequired` and resumes beyond the captured watermark. Every individual delivery is
reauthorized, including every variant of one commit; closing composition wakes blocked feeds.
Live ReplyText/ReplyReset production and contiguous coalescing remain slice 9a qualification.


The same-process worker refusal applies across replacement Engines and SessionService instances
sharing the namespace. Lease release, waiter cancellation and worker timeout MUST NOT release
actual-worker ownership. Store/root close seals registrations before draining and retains root
ownership through canceled or bounded close until actual exit. This handle does not strengthen
cross-process guarantees or replace storage fencing. A resume result belongs to its original
invocation group and remains manually resolvable after closing unknown during recovery.


Archive routing is explicit: list/history use the selected tier; get/entries with tier=None try
live first and fall back only after an actual absent live session. Denial, timeout, integrity and
unavailable failures never cause fallback. Cursor identities include version, owner, query and
tier and cross-tier reuse refuses. All archive reads use copied present actors, finite read-byte,
page and deadline bounds, and current policy before and after adapter I/O. Derived sources must
mark summaries and entry pages partial and cannot claim lossless/restorable capability.

A full archive's read(refs, actor) resolves only records reachable in an owning archived snapshot;
unknown/unreachable refs are explicit failures. Client archive history pages hydrate those refs
under READ_SESSION and exchange-specific READ_CALL_PAYLOAD, including aggregate rechecks.
Legacy provider-id call_detail remains separate from logical model-call authority.

An authoritative sink verifies content closure and metadata/bundle identity, deep-copies accepted
state, and rejects equal-count conflicts, stale prefixes or non-prefix replacement. Retry of the
same snapshot is idempotent. The volatile reference archive has explicit finite session/entry/byte
capacity. Configuring the sink selects retention authority; the explicit export helper also checks
x.tether.archive_session and all original/payload read permissions. Export bounds include total
entries, encoded bytes and deadline, not just page size.

Restore verifies the bundle as the present owner, prepares original history, rechecks current
x.tether.restore_session before atomic journal restoration, and preserves closure, pending,
unknown effects and every original entry. It never approves or dispatches. Reference-preserving
restore currently requires the same provider/codec address profile; cross-provider remapping is
unqualified and refuses rather than rewriting immutable evidence.

Full-bundle restore requires current READ_CALL_PAYLOAD before source fetch, as well as current
READ_SESSION and x.tether.restore_session. Each exchange write and the final journal commit
recheck payload access. Archive history resolves every reachable original reference in the
selected entry page, including nested exchange/annotation refs, under a total reference cap.
Aggregate disclosure runs after the last awaited metadata check. Facts-only sources return
unsupported content slots without fetching raw records; metadata remains partial.

Each provider boundary receives a separate actor snapshot; provider mutation cannot alter later policy subjects or attributes. Query inputs are copied before cursor binding and adapter I/O.

HistoryPage.partial defaults false and propagates the selected archive entry page partial flag, including empty pages. A derived/facts-only history page therefore cannot silently imply a complete empty transcript.


### Caller-aware storage binding

SessionStore.for_actor(actor) is a required pure factory for a non-owning facade. It captures a
copied Actor while preserving the live Credential object and shares capabilities/namespace worker
lifetime/resources. Each operation enforces its owner/query/session.state.owner equals actor.id;
actor.principal is model attribution and never a database ownership subject. Bindings are never
cached by owner, since concurrent same-owner callers may have different credentials. Each provider
acquisition gets a fresh actor snapshot; provider mutation cannot affect later policy subjects.

All caller reads, mutations, owned jobs, retained outcomes, followers/reconnects, archive transfers
and turn leases use the explicit facade. Append/heartbeat/release retain the accepted turn binding.
A bound facade's aclose is non-owning and cannot seal the root or close an application pool. It
cannot expose global maintenance recovery through caller credentials. Unbound root maintenance
and migrations require explicitly configured service authority; caller acquisition failure never
retries using that authority. The owner-only JSONL profile still returns an enforcing facade.

The Postgres profile injects an async connection-acquisition context callback with the current
copied actor; application code owns pool selection, token refresh, roles and optional RLS setup.
Tether retains SQL owner predicates, never chooses or changes a role, never opens a hidden pool,
and never reads connection environment settings. Requested session ids are operation scope, not
ownership proof. Explicit migration uses selected maintenance authority and is separately invoked.
A database/schema namespace has one stable non-secret identity and shared process-local worker
ownership across roots/facades; independent identities stay isolated. Root shutdown seals new
operations and retains namespace ownership until actual workers/I/O finish, even after bounded
or canceled close. External pools remain application-owned. These are required profile gates,
not evidence that Postgres is already implemented or qualified.

The selected Postgres acquisition profile borrows an idle async connection and releases it to
the application source. Tether owns each transaction; an ambient transaction refuses rather
than acknowledging a savepoint before its outer commit. Each acquisition verifies the explicit
schema revision/checksum and stable non-secret namespace identity. No implicit role or search-path
change occurs. Closing one root seals only that root's admission; sibling roots remain usable.
Namespace worker ownership survives final-root close while physical workers still run, so a
replacement root inherits the same busy markers. A release failure after a possible commit
remains uncertain, and caller cancellation does not prove an operation stopped.


### Optional PostgreSQL composition and recovery extension

`tether.stores.postgres.PostgresSessionStore(root, *, actor=None, maintenance=None)` implements
the SessionStore surface over an opened DatabaseRoot. `PostgresHistory(root)` implements its
independent content port; roots MAY be shared or separate. Root construction performs no caller
lookup. A root without a caller binding MUST refuse caller operations with
`ExecutionDenied("caller_scope_required")`. `for_actor(actor)` returns the concrete borrowed
PostgreSQL facade, preserving the current live credential. Rebinding a borrowed facade MUST
retain its owner. Owner/ref create acquires owner scope (`session_id=None`), since an existing
ref can identify a session different from a proposed new id.

Root `recover(now=aware_datetime)` requires explicitly supplied maintenance authority. Acquisition
MUST receive that original actor/credential, never a fabricated actor named after a target owner.
The optional bound `recover_owner(now=aware_datetime)` limits enumeration/mutation to actor.id;
it MUST NOT expose global recovery. Recovery is one transaction. The selected profile bounds
candidates by `StorageLimits.page_limit`, candidate owner/id bytes and aggregate returned
RecoveredTurn bytes by `max_batch_bytes`. Overflow raises StoreError before commit and MUST NOT
report partial success. An expired logical phase is interrupted independently of the lease-job id;
pending approval and unknown-effect facts remain authoritative. Naive datetimes refuse.

A leased execution prefix MUST remain count/byte bounded as it grows. Stable-key retries add no
row bytes or count. SQL checks the final frame, including lease-holder identity, together with
retained and inserted canonical row bytes before commit. A proven bound rejection writes nothing.
After known append commit, failed/canceled local staging or final validation MUST seal the handle
and report CommitUnknown (caller cancellation remains cancellation); LeaseLost remains terminal.
Cached state cannot authorize continued dispatch after these failures. Reopen reconstructs facts.

DatabaseRoot close MUST seal execution admission through that root before awaiting shutdown.
Independent roots share a concrete namespace actual-worker registry but retain separate admission
gates. Closing one root MUST NOT seal sibling submissions or erase retained workers. Already
admitted preparation MUST either register its physical Future or refuse before executor submission.
Fresh-token lease cleanup MAY clear metadata while physical work remains; it MUST retain actual
worker ownership and block local replacement until actual exit. Stale cleanup MUST NOT alter a
replacement lease. Cleanup failure MUST preserve an already-active primary exception; it MUST NOT
substitute maintenance credentials.

Example composition (application-supplied `source`, actors and opened database):

```python
store = PostgresSessionStore(database, maintenance=maintenance_actor)
caller = store.for_actor(actor)
session = await caller.create(actor.id, SessionInit(ref="fabricated"))
# For a restricted maintenance deployment, use the explicit owner extension:
recovered = await caller.recover_owner(now=aware_now)
```

These optional adapter additions do not change SESSION_CONTRACT_VERSION4 or require other stores
to implement recover_owner. Application pools, authentication refresh, role/RLS configuration,
external effect fencing and deployment lifetime remain outside the library.


### Optional embedded PostgreSQL ownership

`tether.stores.postgres.embedded` exports EmbeddedConfig, EmbeddedEndpoint and EmbeddedPostgres.
This optional composition does not change SESSION_CONTRACT_VERSION4. Every EmbeddedConfig field is
required: bootstrap_user (nonempty, no NUL, at most 63 UTF8 bytes), absolute data_dir and runtime_dir
(Path), startup_seconds, shutdown_seconds and poll_seconds (finite positive seconds), and max_roots
(positive integer). The parent of data_dir and runtime_dir itself MUST already be existing directories. data_dir MAY
be absent. Runtime MUST be outside data; the
selected Unix socket path bound is 85 encoded bytes for the owned runtime subdirectory. The helper
MUST NOT read environment settings. Unsupported platform/interpreter/binary profiles raise Unsupported.

`await EmbeddedPostgres.start(config)` initializes an empty data directory or verifies a managed
PostgreSQL18 directory. The bootstrap database username is explicit, not an application caller or
maintenance identity. TCP listening MUST be disabled. The selected local native profile uses trusted
Unix socket authentication inside the owned 0700 runtime directory. Applications requiring a different
native authentication/deployment profile MUST supply a separately administered server.

Canonical data-directory ownership MUST exclude duplicate helpers, including symlink aliases and
other runtime directories. The managed marker MUST be inside the initialized data instance. Missing,
partial, malformed, oversized or unmarked existing data MUST refuse; the helper MUST NOT repair,
adopt or delete it. An existing postmaster.pid MUST refuse even if its process may be dead; explicit
external verification/cleanup belongs to the operator. No arbitrary PID read from disk may be killed.

`server.endpoint` is a pure owning-loop property exposing host (owned Unix socket directory), port
(5432), dbname (postgres) and user (configured bootstrap_user). It MUST refuse before readiness or
after shutdown starts. It confers no application authorization. The application builds its own async
ConnectionSource, credentials and optional pools. `await server.open_root(source, *, actor, schema,
limits=None)` registers a borrowed DatabaseRoot without migration. The current copied actor MUST
reach source acquisition; refusal MUST NOT trigger another source or maintenance fallback.
max_roots bounds registered roots plus in-flight root openings for this helper lifetime. Shutdown
racing root opening MUST seal/register the root and refuse publication rather than return a usable
execution gate. A failed root opening's retained acquisition MUST still delay native shutdown.

`await server.aclose() -> bool` MUST seal admission first, retain actual startup/connection/worker
ownership and request native fast shutdown only after registered roots and acquisitions drain.
True means owned resources drained and native process exit observed; False means the configured
caller wait elapsed and cleanup continues under retained ownership. Cancellation does not cancel
cleanup. Canceled initialization MUST retain ownership through normal initdb completion, including
its native child processes. Successful initialization MAY remain for a later explicit start. Data
files and marker MUST persist; only the helper-owned runtime subdirectory is removed. After confirmed
native exit, runtime removal failure MUST still release the data lock and report the cleanup error.
An already-removed runtime directory counts as cleaned. Wrong-loop use MUST refuse before mutation.

Error reasons include embedded_directory_busy/embedded_existing_server (RootBusy), unmanaged or
invalid marker (IntegrityError), partial initialization, unavailable/closed lifetime, capacity and
startup failures (StoreError), and invalid explicit configuration (ValueError). Native output MUST
remain controlled; errors MUST NOT include SQL, credentials or private connection configuration.
Retry after RootBusy requires actual prior-owner exit; malformed data requires explicit operator
intervention. Parent-process death does not promise automatic server cleanup or automatic adoption.

Example, with application-built source using server.endpoint:

```python
server = await EmbeddedPostgres.start(explicit_config)
try:
    source = application_source(server.endpoint)
    await migrate(source, actor=maintenance_actor, schema="tether")
    database = await server.open_root(source, actor=actor, schema="tether")
    store = PostgresSessionStore(database).for_actor(actor)
finally:
    drained = await server.aclose()
```

A False close result requires continued application supervision; it does not permit replacement.


### Reusable storage qualification entry points

`tether.testing.conformance.live_store_suite(factory)` and `archive_suite(factory)` are async
entry points without a pytest runtime dependency. A pytest/anyio test MAY await them. Factories
MUST provide isolated fabricated test namespaces and own their resources; suites MUST NOT open
implicit pools, choose application credentials, migrate schemas or administer native servers.
A suite MUST report exactly which checks ran and which adapter/profile qualifications remain.
Passing a logical port subset MUST NOT be represented as full durable, cross-process, fault or
release qualification. Adapter-specific process and fault suites remain required alongside shared
logical cases; absent evidence MUST be explicit rather than a silently skipped success.


The logical live factory has shape `Callable[[], AbstractAsyncContextManager[LiveStoreFixture]]`.
LiveStoreFixture is a frozen dataclass with store: SessionStore (an unbound factory root), actor: Actor
and foreign: Actor with distinct ids. The first entry MUST use an isolated namespace. Re-entering the
factory for a declared durable profile MUST reopen that same namespace, with the same actor identity;
it MAY refresh credentials. The factory owns explicit migration, pools, roots and physical cleanup.

The archive factory has shape
`Callable[[ArchivedSession], AbstractAsyncContextManager[ArchiveFixture]]`. ArchiveFixture contains
source: ArchiveSource, actor: Actor, foreign: Actor and optional sink: ArchiveSink. actor.id MUST
match the sample owner and foreign.id MUST differ. The fixture supplies caller credentials; the
suite passes isolated actor copies while preserving their live Credential objects. The sample is fabricated canonical Tether
history-v1 content. A source-only fixture MUST preload its sample. A supplied sink MUST declare
writable and is checked independently of source capabilities, including a read-only source facade.
The suite MUST keep its expected sample private and pass independent deep copies at each mutable
factory/sink boundary; a mutating implementation MUST NOT alter the losslessness oracle.

Both entry points accept keyword-only timeout_seconds (finite positive float, default 30) and return
SuiteReport: schema_version=1, suite (live_store or archive), capabilities (copied source/store
capability metadata), sink_capabilities (copied optional sink metadata, independent of source writable), checks (tuple of executed
stable check ids), and required_external (tuple of still-required profile checks). Reports MUST NOT
contain content or credentials. Missing external qualifications MUST prevent a full-profile claim.
The logical snapshot/feed case permits either complete before/after prefix; it does not establish
deterministic lock contention. Full projection reconstruction, approval/write contention, stale
lease/recovery, no-scan/keyset, context closure, bounds and physical/process faults retain separate
required qualifications. Check failures use ConformanceFailure with a stable check id; unexpected
adapter exceptions and timeouts propagate to the calling test. No case filter is selected.


### Optional atomic mutation receipts (companion version1)

The optional actor-bound MutationReceipts companion preserves SessionStore.create/update return
signatures. StoreCapabilities.mutation_receipts is0 (unavailable, default) or1 (companion version1).
A version1 facade MUST implement async create_with_receipt(owner, init) -> CreateReceipt and
update_with_receipt(session_id, owner, patch) -> UpdateReceipt. Discovery MUST occur on the current
for_actor(actor) facade; owner, loop, closed-state, credential and resource-lifetime rules remain
identical. Capability/method mismatch refuses before dispatch. An invalid returned version or
receipt refuses after the call, without an old-API fallback or a second mutation attempt.

MUTATION_RECEIPT_VERSION=1. CreateReceipt and UpdateReceipt contain version=1, a copied
SessionSummary, disposition and optional MutationCommit. MutationCommit contains owner, session_id,
seq (positive exact owner change sequence) and entries (copied accepted metadata Entry tuple,
maximum3). Its scope/seq MUST match the returned summary. Entries are only title/tag session facts
and settings changes, in dense accepted order ending at summary.entry_count-1. No history refs,
call manifests or executable entries belong in this companion. Selected adapters preserve configured StorageLimits byte/index bounds (max_batch_bytes for
durable batches/frames and max_index_bytes for the materialized volatile index); no transcript is
returned. Consumers additionally enforce their configured response bounds.

Create disposition is created or existing. Created has a commit with empty entries and an empty
journal summary; it proves this call created that session in the same accepted transaction. Existing
has no commit, even after intervening turns/settings changes. Genesis has its own stable identity
and MUST NOT invent an entry position. Update disposition is accepted or noop. Accepted has a
nonempty exact metadata commit and its summary from that transaction, before lease release allows
a concurrent writer. Noop has no commit and means no metadata entries were appended; lease
acquisition may still recover an expired phase. Equal-value nonempty patches retain append
semantics and yield accepted. Existing summary APIs remain unchanged.

Receipts return only after known acknowledgment of the corresponding mutation. Rejection,
rollback, cancellation or uncertain acknowledgment does not return an accepted receipt. Receipt
construction MUST NOT introduce another acquisition, writer, pool, schema or storage telemetry
callback. An adapter without the companion remains usable; complete selected-profile semantic
genesis/settings audit qualification requires it. Receipt values are content-bearing internal
results, not audit packets: audit copies only registered identifiers, positions, categories/counts.
Semantic settings identity remains per accepted Entry under CONTRACT-002.

Example: create_with_receipt(owner, SessionInit(ref="fabricated")) returns created with seq7 and no
entries; after a title update at seq8, repeating the same ref returns existing with the current
summary and no commit. update_with_receipt(..., SessionPatch()) returns noop; an explicit
title=None appends the clearing fact and returns accepted with that exact Entry/seq. Conformance
covers stable-ref races, intervening updates, empty/equal patches, concurrent exact frontiers,
unknown acknowledgments, copied/bounded entries and current actor/credential forwarding.


ClientLimits.max_mutation_receipt_bytes is an optional positive setting defaulting to4MiB. It bounds
receipt serialization/validation/copy before semantic audit; max_read_bytes still governs the public
summary response. An over-budget receipt raises ReadLimit("mutation_receipt_bytes"); invalid
returned shape/version/scope raises StoreError("mutation_receipt_invalid"). No legacy fallback
follows a possibly committed receipt call. These receipt errors may follow an accepted storage
mutation and do not undo it. A later READ_SESSION denial or public response-size refusal occurs
after valid known receipt admission under CONTRACT-002. Root/credential/owner and existing
policy actions remain unchanged. Optional receipt mode is selected for audit-enabled commands;
applications without audit retain the legacy summary call path.
