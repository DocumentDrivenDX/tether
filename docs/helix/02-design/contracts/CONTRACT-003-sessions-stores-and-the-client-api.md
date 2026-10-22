---
ddx:
  id: CONTRACT-003
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
    - id: architecture
      kind: informed_by
---

# CONTRACT-003: Sessions, Stores, and the Client API

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-003
**Type**: library (Python ports and API)
**Version**: v1
**Related**: ADR-001, ADR-004, ADR-005, ADR-010, ADR-011, ADR-014, ADR-015, ADR-018, ADR-019, ADR-020, CONTRACT-001, CONTRACT-002

## Purpose

This contract fixes the session log, the two storage ports that hold it (a live store and an archive), and the client API through which a user interface lists sessions, reads session data, follows live changes, and gives commands. The client API is the boundary between tether and any interface (ADR-004); the live store is the boundary between tether and its hot storage (ADR-005); the archive is the boundary between tether and cold or derived history (ADR-019). All are asynchronous (ADR-001). It also states what each store guarantees, by declared capability, so a recovery guarantee is never claimed for a store that cannot keep it.

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
#   message          message: Message
#   turn_started     spec_id: str; spec_version: str; spec_digest: str | None; trace_id: str | None; resumes: str | None
#   turn_ended       stop_reason: StopReason; reason: str | None
#   model_call       tier: str; request_id: str | None; status: Literal["ok", "error"]; duration_ms: int
#                    usage: TokenUsage | None; scope: dict[str, tuple[str, ...]]      # the scope values held at the call (identifiers)
#   tool_call        invocation_id: str; call_id: str; tool: str
#                    status: Literal["started", "ok", "error", "denied", "withheld", "invalid_arguments", "interrupted"]
#                    duration_ms: int | None; reason: str | None
#                    tool_digest: str | None; args_digest: str | None; approval_index: int | None    # on `started`
#   confirmation     invocation_id: str; tool: str; state: Literal["requested", "approved", "declined", "superseded", "expired", "invalid"]
#                    args_digest: str; tool_digest: str; by: str | None; expires_at: datetime | None
#   resolved         invocation_id: str; outcome: Literal["completed", "not_completed"]; by: str | None     # reserved (P1)
#   settings         changes: dict[str, JsonValue]
#   session          event: Literal["created", "closed", "titled", "tagged"]; value: JsonValue | None
#   compaction       reserved (PRD FR-35)
#   x.<provider>.<kind>   reserved for providers; no writer in v1
```

| Rule | Requirement |
|------|-------------|
| Immutable | An entry MUST NOT change after it is written. A rewrite of history is a new session forked from an entry. |
| Positions | `index` is dense and increasing within a session. The store assigns it on append. |
| Facts, not content | `model_call`, `tool_call`, and `confirmation` entries carry facts (identifiers, digests, tiers, statuses, sizes, reasons) and never message or tool content; content is in `message` entries. A credential is in no entry. |
| Invocation id | `invocation_id` is derived by the engine from the session id, the index of the committed assistant entry that holds the call, and the call's position in it, so it is identical on every attempt and across resume (ADR-014). It is not the model's `call_id`, which can repeat. |
| Idempotency keys | An append MAY carry a `key`. The store MUST ignore an append whose `(session, key)` exists and return the existing entry. The engine uses `intent:<invocation_id>`, `result:<invocation_id>`, `approval:<invocation_id>`, and `request:<request_id>`. |
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
    preview: str | None                               # a bounded-length snippet of the last message; MAY be off by configuration
    usage: TokenUsage                                 # totals over the session
    spec_id: str | None; pending: bool                # an approval is waiting
    last_seq: Seq                                     # the change sequence of the last change to this session

class SessionView(BaseModel):
    summary: SessionSummary; settings: dict[str, JsonValue]
    specs: dict[str, SpecRef]                         # the specs the session has run: id, version, digest
    pending: PendingConfirmation | None; tools: tuple[ToolInfo, ...]

class SessionQuery(BaseModel):
    owner: str                                        # required
    ref: str | None = None
    status: frozenset[str] | None = None; tags_all: tuple[str, ...] = ()
    updated_after: datetime | None = None; updated_before: datetime | None = None
    text: str | None = None                            # title and preview; a store MAY decline with `Unsupported`
    order: Literal["updated_desc", "created_desc", "created_asc"] = "updated_desc"

class Page(BaseModel, Generic[T]):  items: tuple[T, ...]; next: Cursor | None
class EntryPage(BaseModel):  items: tuple[Entry, ...]; next_start: int | None
```

| Rule | Requirement |
|------|-------------|
| Projections | A summary is a projection of the log. The store MUST update it in the same transaction as the append that changes it, and a conformance test MUST be able to rebuild it from the log and find it equal. |
| Cheap lists | A list MUST NOT read entries and MUST NOT compute a total. |
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

class LiveStore(Protocol):
    capabilities: StoreCapabilities
    # reads (the client API's backing)
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int) -> Page[SessionSummary]: ...
    async def entries(self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool) -> EntryPage: ...
    def changes(self, owner: str, *, session_id: str | None, since: Seq | None) -> AsyncIterator[PersistedChange]: ...
    async def find(self, owner: str, ref: str) -> SessionSummary | None: ...
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
| Approvals wait without a lease | A session waiting for an approval holds no lease, so it survives a restart; an answer takes the lease like any turn. |

**Recovery guarantees, by declared capability.** These are the only guarantees; a store that does not meet a row's condition does not get its guarantee.

| Store (declared) | What holds | Not guaranteed |
|------------------|-----------|----------------|
| `durability=none`, `lease=process` (the in-memory store) | Atomicity and ordering within one process; recovery of a failed turn task in a living process | Anything across a process death. "A killed turn resumes" does not apply. |
| `durability=process` or `os`, `fencing=true` | Committed steps survive; an intent entry is present before an unsafe tool runs; a stale runner cannot append; recovery marks a dead turn interrupted | Exactly-once external effects (ADR-014) |
| `durability=process` only | Survives a process kill | A commit just before an operating-system crash can be lost; an effect may then be unrecorded and its intent absent, so "not started" may be wrong |
| `fencing=false` | Single-runner behavior if the application guarantees one runner | Protection from a stale runner appending after recovery; a double execution is possible |
| `lease=process` with several processes | Nothing across processes | One turn per session across processes; use a `cross_process` store |

A `Tether` configured to resume (`resume` other than `off`) MUST refuse a store that declares `durability=none` or `fencing=false` with `UnsupportedStore`.

### 4. The archive ports

```python
class ArchiveCapabilities(BaseModel):
    kind: Literal["authoritative", "derived"]
    writable: bool                                    # has a sink half
    lossless: bool                                    # holds every entry kind
    restorable: bool                                  # can restore a session to the live store
    call_detail: bool                                 # can return a model call's request and response by provider request id
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
    async def call_detail(self, request_ids: Sequence[str], *, actor: Actor) -> Mapping[str, CallDetail | Pending | Denied | Unavailable]: ...
    async def restore(self, session_id: str, *, actor: Actor) -> ArchivedSession: ...   # only if `restorable`

class CallDetail(BaseModel):  request_id: str; status: str | None; request: JsonValue | None; response: JsonValue | None; usage: TokenUsage | None
```

| Rule | Requirement |
|------|-------------|
| Optional | A deployment without an archive works. No turn, list of live sessions, or recovery depends on one. |
| Never in the turn's path | The engine calls `ArchiveSink.archive` only after a session is closed or idle by the application's policy, never inside a turn, and a failure is audited and never raised into a turn. |
| Authoritative | An authoritative archive written by tether MUST hold every entry of the session it archived, unchanged. |
| Derived | A derived archive reconstructs what an external log holds (content and usage). It MUST declare `lossless=false` and `restorable=false`, MUST mark every summary and entry page it returns `partial`, and MUST NOT present tool executions, approvals, or intents it does not have. |
| As the caller | Every read takes the calling actor and uses its credential (ADR-011). A read is bounded by a time limit. |
| Not silently empty | A read that is not finished is reported `Pending`, one the caller may not make `Denied`, and one that failed `Unavailable`. It is never an empty result. |
| Read-only unless restored | An archived session cannot take a turn. `restore` is offered only by a `restorable` archive and creates the session in the live store with its entries, in one atomic commit, under the same `ref`. |
| Tiers | A summary carries `tier`. A cursor belongs to its tier. The API promises no merged ordering across tiers. |

### 5. Changes and the change feed

```python
class PersistedChange(BaseModel):  seq: Seq; session_id: str; kind: Literal["updated", "removed"]; summary: SessionSummary | None; entries: tuple[Entry, ...]
# the feed a client sees:
class SessionUpdated(BaseModel):  seq: Seq; summary: SessionSummary
class SessionRemoved(BaseModel):  seq: Seq; session_id: str
class EntriesAppended(BaseModel):  seq: Seq; session_id: str; entries: tuple[Entry, ...]
class ReplyText(BaseModel):  session_id: str; turn_id: str; text: str          # live only; never persisted; no seq
class Lagged(BaseModel):  missed: int                                           # this follower fell behind and lost live events
class ResyncRequired(BaseModel):  reason: Literal["too_old", "gap"]
Change = SessionUpdated | SessionRemoved | EntriesAppended | ReplyText | Lagged | ResyncRequired
```

| Rule | Requirement |
|------|-------------|
| Resume | `follow(since=N)` yields persisted changes with `seq > N` in order, then continues live. `since=None` means from now, with no replay. If `N` is older than the retained window, the feed yields `ResyncRequired("too_old")` first and then continues live; the follower lists again. |
| Persisted versus live | `SessionUpdated`, `SessionRemoved`, and `EntriesAppended` carry a `seq` and are authoritative. `ReplyText` is a live convenience: a follower that misses it loses nothing, because the finished message arrives as an entry. |
| Coalescing | When a follower's queue backs up, adjacent `ReplyText` events of one turn MUST be merged before any event is dropped. Persisted changes are never dropped from a retained window; a follower too slow to keep up receives `Lagged` and, if its position leaves the window, `ResyncRequired`. |
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
    async def call_detail(self, session_id: str, request_ids: Sequence[str]) -> Mapping[str, CallDetail | Pending | Denied | Unavailable]: ...
    def follow(self, *, session_id: str | None = None, since: Seq | None = None) -> AsyncIterator[Change]: ...
    async def find(self, ref: str) -> SessionSummary | None: ...
    async def create(self, *, ref: str | None = None, spec: str | None = None, title: str | None = None,
                     tags: Sequence[str] = (), settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def send(self, session_id: str, text: str, *, request_id: str | None = None,
                   attachments: Sequence[AttachmentRef] = (), spec: str | None = None) -> TurnHandle: ...
    async def confirm(self, session_id: str, invocation_id: str, answer: Answer) -> TurnHandle | None: ...
    async def resume(self, session_id: str) -> TurnHandle: ...
    async def resolve(self, session_id: str, invocation_id: str, outcome: Literal["completed", "not_completed"]) -> SessionSummary: ...   # reserved (P1)
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
    def client(self, actor: Actor) -> Client: ...
    async def __aenter__(self) -> "Tether": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

| Rule | Requirement |
|------|-------------|
| One object | A UI needs only `Client`. It never imports a store or the engine. |
| Identity | `client(actor)` binds an actor. Commands that start model calls (`send`, `confirm`, `resume`) use its credential for that call and never persist it (ADR-011). A follower needs no credential. |
| Send | `send` commits the user's message and the turn start, then returns a `TurnHandle` without waiting for the turn to finish. The turn runs as a task owned by `Tether`; the consumer follows it through `follow` or awaits `result`. |
| Idempotent send | `send` with a `request_id` that exists in the session with the same text returns the existing turn's handle; with different text it raises `RequestConflict`. The key is `request:<request_id>` (ADR-018). |
| One turn | `send` to a session with a running turn raises `TurnInProgress`; the running turn is unaffected. |
| Supersession | `send` to a session with a pending approval supersedes it: each pending call gets a denied result with reason `superseded`, then the new turn starts (ADR-015). |
| Confirm | `confirm` answers the pending approval named by `invocation_id`. It takes the lease, checks the answer's key, and resumes the turn as a new task. It returns `None`, and changes nothing, when `invocation_id` is not pending or another answer already won. |
| Resume | `resume` continues the session's interrupted turn as a new turn (ADR-010). It raises `NotInterrupted` when there is none, `ResumeLimit` when the chain limit is reached, and `UnsupportedStore` as in section 3. |
| Cancel | `cancel` cancels the session's running turn and returns whether there was one. Steps already committed remain. |
| Shutdown | Leaving `Tether`'s context cancels running turns, waits for their cleanup, and closes the stores. |
| Settings | `update` records a `settings` or `session` entry and the change appears in the feed. |
| Defaults | A `SessionQuery` of `None` lists the owner's live sessions, most recently updated first. |
| Application references | A `ref` ties a session to an application's own record; tether never reads the application's records (ADR-018). |

**`Tether` options.** `resume` (`"off"`, `"report"`, or `"auto"`; the proposed default is `"report"`), `resume_actor` (a function that returns an actor with a live credential for a session, or `None`, used only with `"auto"`), `recover_interval`, `max_resumes` (the proposed default is 3), `lease_window` and `heartbeat_interval`, `approval_ttl` (none by default), `archive` and `archive_on` (a policy for when a session is archived), and the archive read time limit. The numeric defaults other than those named are supplied by the owner after the first baseline.

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

The package ships `InMemoryStore` (a live store declaring `durability="none"`, `lease="process"`, `fencing=true`) and `InMemoryArchive` (authoritative, lossless, restorable). A Postgres live store ships in an extra (ADR-020); a SQLite store is optional. `tether.testing.conformance.live_store_suite(factory)` runs, as asynchronous tests, with each durable test run only for a store that declares the capability it needs: atomic commit (a failed append writes nothing), summary equals the rebuilt projection, dense positions, idempotent keys, lease exclusivity across tasks and, for `cross_process` stores, processes, fencing (a stale append is refused), lost-lease refusal, idempotent recovery, owner scoping, `ref` uniqueness and idempotent create, keyset paging stability under concurrent writes, change-feed order, resume and resync, coalescing, backpressure isolation, that no list reads entries, and, for `durability` of `process` or `os`, that a committed append survives closing and reopening the store (a real-process kill test is part of the Postgres and SQLite suites). `archive_suite(factory)` runs: idempotent archive, authoritative losslessness, derived marking `partial`, read-as-caller, pending, denied, and unavailable reporting, and restore for a restorable archive. Operating-system-crash durability cannot be tested in continuous integration, and the guarantee table says so.

## Precedence and Compatibility

- Versioning: one integer, `SESSION_CONTRACT_VERSION`. A new entry kind is compatible, and a reader MUST ignore an entry kind it does not know.
- Ordering or precedence: this contract governs persistence and the client API; CONTRACT-002 governs which entries the engine writes and when, and its gate sequence.
- Backward-compatibility rules: adding an optional field, an entry kind, a capability, or a `Change` variant that a follower may ignore is compatible. Changing a signature, a rule, a guarantee, or a cursor format is a new version. A cursor format change invalidates outstanding cursors, which a store refuses with `InvalidCursor`.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| `UnknownSession` (missing or not the owner's) | Raised with no distinguishing detail | No | The caller answers as for a missing session |
| `TurnInProgress` | Raised by `turn` and `send` | Yes, later | One turn per session at a time |
| `SessionClosed` | Raised by `turn` and `send` | No | Create a session |
| `LeaseLost` | Raised by `append`; nothing written | No | Recovery resolves the turn |
| `RequestConflict` | Raised by `send` | No | Use a new `request_id` |
| `NotInterrupted`, `ResumeLimit` | Raised by `resume` | No | The session has nothing to resume, or the chain limit stops a crash loop |
| `UnsupportedStore` | Raised at construction or by `resume` | No | Use a store that declares durable commits and fencing |
| `InvalidCursor` | Raised by `list` | No | List again from the first page |
| `Unsupported` | Raised for a query feature the store lacks (such as `text`) | No | Use a different filter |
| `NotRestorable` | Raised by `restore` | No | The archive is derived or not restorable |
| A follower falls behind | `Lagged`, then `ResyncRequired` if its position left the window | Yes | List again, then follow from the new position |
| A store commit fails | The append raises `StoreError` and writes nothing | The engine does not retry a failed commit | The engine stops the turn; if the tool already ran, the invocation's outcome is unknown (ADR-014); `TurnHandle.result()` raises `StoreError`; if the store stays unavailable the lease expires and recovery marks the turn interrupted |
| The archive is slow or down | A read returns `Pending` or `Unavailable`; a sink failure is audited | The application's policy | No turn is affected |
| The process dies mid-turn | The lease expires; `recover` appends `turn_ended: interrupted` | n/a | The consumer sees the session as interrupted and a present caller resumes it |

## Examples

```python
async with Tether(engine=engine, store=InMemoryStore()) as tether:
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
tether = Tether(engine=engine, store=PostgresStore(pool), archive=InferenceTableArchive(...))   # the archive ships with the Databricks profile
detail = await client.call_detail(session_id, [entry.request_id for entry in model_call_entries])
```

## Non-Normative Notes

- Position-addressed entries and keyset-addressed sessions are chosen so no read needs a scan. A UI that wants "jump to page N" of sessions must search or filter instead.
- A server adapter (HTTP, WebSocket, server-sent events) maps `Client` calls to routes and `follow` to a stream. Its authentication and the choice of the actor are its own.
- The summary `preview` and `text` search reveal message content to a list reader. A deployment that must not do that turns the preview off.
- The live and archive ports follow a pattern in the first downstream platform: its session store keeps keys and facts (request ids, usage, statuses), and a bounded, per-user reader returns the gateway's logged request and response on demand. That reader is the model for a derived archive.
- Whether a live store may hold a model call's content by reference only, resolving it from a derived archive when read, is open (ADR-019). Version 1 live stores hold content.

## Open Items

| Item | Owner |
|------|-------|
| The retained window for change records, the default follower queue size, the lease window, the heartbeat interval, and the recovery interval | Erik LaBianca, from the first benchmarks |
| Whether `list` should accept an `as_of` position for stable paging under heavy writes beyond the keyset guarantee | Erik LaBianca |
| Fork and rewind (PRD FR-15) entry and API shape | Erik LaBianca, when FR-15 is scheduled |
| Content by reference in a live store backed by a derived archive | Erik LaBianca, in the Databricks profile slice |
| The visibility watermark (section 5) | Erik LaBianca, when a consumer needs it |
