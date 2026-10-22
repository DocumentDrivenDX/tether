---
ddx:
  id: CONTRACT-003
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
    - id: ADR-004
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: CONTRACT-001
      kind: informed_by
    - id: CONTRACT-002
      kind: informed_by
    - id: architecture
      kind: informed_by
---

# CONTRACT-003: Sessions and the Client API

> Examples are fabricated; no credential, host, or private data appears here.

**Contract ID**: CONTRACT-003
**Type**: library (Python port and API)
**Version**: v1
**Related**: ADR-001, ADR-004, ADR-005, CONTRACT-001, CONTRACT-002

## Purpose

This contract fixes the session log and the store port that persists it, and the client API through which a user interface lists sessions, reads session data, follows live changes, and gives commands. The client API is the boundary between tether and any interface (ADR-004); the store port is the boundary between tether and storage (ADR-005). Both are asynchronous (ADR-001).

## Scope and Boundaries

- In scope: the entry types; summaries and views; the store port and the turn handle; the change feed and its resume rules; queries and cursors; the client API and its commands; ownership and error rules; performance rules; the store conformance suite.
- Out of scope: the engine's behavior (CONTRACT-002); the model port (CONTRACT-001); any wire format of a server adapter; the storage schema of a particular store.
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
# NewEntry: the same fields as the kind below, without `index` and `at` (the store assigns them), plus an optional `key` (idempotency, below);
#   `turn_id` is supplied by the engine.
# kinds:
#   message          message: Message
#   turn_started     spec_id: str; spec_version: str; trace_id: str | None
#   turn_ended       stop_reason: StopReason; reason: str | None
#   model_call       tier: str; request_id: str | None; status: Literal["ok", "error"]; duration_ms: int; usage: TokenUsage | None
#   tool_call        call_id: str; tool: str; status: Literal["ok", "error", "denied", "withheld", "invalid_arguments"]; duration_ms: int; reason: str | None
#   confirmation     state: Literal["requested", "approved", "declined"]; calls: tuple[str, ...]
#   settings         changes: dict[str, JsonValue]
#   session          event: Literal["created", "closed", "titled", "tagged"]; value: JsonValue | None
#   x.<provider>.<kind>   data: dict[str, JsonValue]
```

| Rule | Requirement |
|------|-------------|
| Immutable | An entry MUST NOT change after it is written. A rewrite of history is a new session forked from an entry. |
| Positions | `index` is dense and increasing within a session. The store assigns it on append. |
| Facts, not content | `model_call`, `tool_call`, and `confirmation` entries carry facts (identifiers, tiers, statuses, sizes, reasons) and never message or tool content; content is in `message` entries. |
| Idempotency | A `NewEntry` MAY carry a `key`. The store MUST ignore an append whose `(session, key)` already exists and return the existing entry, so a resumed or retried step does not duplicate. |

### 2. Summaries, views, queries, and pages

```python
class SessionSummary(BaseModel):                      # small; kept current by the store on every write
    id: str; owner: str; title: str | None; tags: tuple[str, ...]
    status: Literal["idle", "running", "waiting", "interrupted", "closed"]
    created_at: datetime; updated_at: datetime; last_turn_at: datetime | None
    entry_count: int; message_count: int; turn_count: int
    preview: str | None                               # a bounded-length snippet of the last message; MAY be off by configuration
    usage: TokenUsage                                 # totals over the session
    spec_id: str | None; pending: bool                # an approval is waiting
    last_seq: Seq                                     # the change sequence of the last change to this session

class SessionView(BaseModel):
    summary: SessionSummary; settings: dict[str, JsonValue]
    specs: dict[str, SpecRef]                         # the specs the session has engaged: id, version
    pending: PendingConfirmation | None; tools: tuple[ToolInfo, ...]

class SessionQuery(BaseModel):
    owner: str                                        # required
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
| Keyset paging | A page's `next` is an opaque keyset cursor over the query's order; a store MUST NOT page by offset. A cursor from a different query is refused. |
| Entry paging | `entries` addresses by position: items from `start` (inclusive), in order or reversed, at most `limit`; `next_start` is the next position or `None`. |
| Owner scope | Every operation takes the owner and MUST see only that owner's sessions. |

### 3. The store port

```python
class SessionStore(Protocol):
    # reads (the client API's backing)
    async def get(self, session_id: str, owner: str) -> SessionView | None: ...
    async def list(self, query: SessionQuery, *, cursor: Cursor | None, limit: int) -> Page[SessionSummary]: ...
    async def entries(self, session_id: str, owner: str, *, start: int, limit: int, reverse: bool) -> EntryPage: ...
    def changes(self, owner: str, *, session_id: str | None, since: Seq | None) -> AsyncIterator[PersistedChange]: ...
    # writes
    async def create(self, owner: str, init: SessionInit) -> SessionSummary: ...
    def turn(self, session_id: str, owner: str, *, turn_id: str) -> AbstractAsyncContextManager[TurnLog]: ...
    async def update(self, session_id: str, owner: str, patch: SessionPatch) -> SessionSummary: ...
    async def recover(self, *, now: datetime) -> tuple[RecoveredTurn, ...]: ...

class TurnLog(Protocol):                              # the write handle for one turn; holds the session lease
    session: SessionState                             # a snapshot of the log at the lease (CONTRACT-002)
    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]: ...    # one atomic commit
    async def heartbeat(self) -> None: ...
```

| Rule | Requirement |
|------|-------------|
| Atomic commit | `append` MUST write its entries, the updated summary, and the change records in one atomic commit, and MUST return only after the commit is durable. |
| Lease | `turn` takes a per-session lease for the block, across tasks, threads, and processes, and raises `TurnInProgress` when it is held. A lease expires when its heartbeat is older than the store's lease window. The lease is released when the block exits for any reason. |
| Lost lease | After a lease expires, an `append` MUST raise `LeaseLost` and write nothing. |
| Recovery | `recover` finds sessions whose lease expired with a turn open and appends a `turn_ended` entry with stop reason `interrupted` for each, in one commit, and returns what it found. It is idempotent. |
| Ownership | An owner that does not own the session, and an id that does not exist, MUST both raise `UnknownSession` with no distinguishing detail. |
| Closed | A closed session accepts no turn and `turn` raises `SessionClosed`. |
| Change records | Every commit that changes a summary writes a change record with the next per-owner `Seq`. The store retains change records for a configured window. |

### 4. Changes and the change feed

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

### 5. The client API

```python
class Client(Protocol):                               # obtained as `tether.client(owner)`; every call is scoped to that owner
    async def list(self, query: SessionQuery | None = None, *, cursor: Cursor | None = None, limit: int = 50) -> Page[SessionSummary]: ...
    async def get(self, session_id: str) -> SessionView: ...
    async def entries(self, session_id: str, *, start: int = 0, limit: int = 100, reverse: bool = False) -> EntryPage: ...
    def follow(self, *, session_id: str | None = None, since: Seq | None = None) -> AsyncIterator[Change]: ...
    async def create(self, *, spec: str | None = None, title: str | None = None, tags: Sequence[str] = (),
                     settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def send(self, session_id: str, text: str, *, attachments: Sequence[AttachmentRef] = (),
                   spec: str | None = None) -> TurnHandle: ...
    async def confirm(self, session_id: str, call_id: str, answer: Answer) -> TurnHandle | None: ...
    async def cancel(self, session_id: str) -> bool: ...
    async def update(self, session_id: str, *, title: str | None = None, tags: Sequence[str] | None = None,
                     settings: Mapping[str, JsonValue] | None = None) -> SessionSummary: ...
    async def close(self, session_id: str) -> SessionSummary: ...

class TurnHandle(Protocol):
    turn_id: str
    async def result(self) -> TurnResult: ...
    async def cancel(self) -> None: ...

class Tether:                                          # the composition: engine, store, change bus; an async context manager that owns turn tasks
    def client(self, owner: str) -> Client: ...
    async def __aenter__(self) -> "Tether": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

| Rule | Requirement |
|------|-------------|
| One object | A UI needs only `Client`. It never imports a store or the engine. |
| Send | `send` commits the user's message and the turn start, then returns a `TurnHandle` without waiting for the turn to finish. The turn runs as a task owned by `Tether`; the consumer follows it through `follow` or awaits `result`. |
| One turn | `send` to a session with a running turn raises `TurnInProgress`; the running turn is unaffected. |
| Confirm | `confirm` answers the pending call and resumes the turn as a new task; it returns `None` when `call_id` is not the pending call, and changes nothing. |
| Cancel | `cancel` cancels the session's running turn and returns whether there was one. Steps already committed remain. |
| Shutdown | Leaving `Tether`'s context cancels running turns, waits for their cleanup, and closes the store. |
| Settings | `update` records a `settings` or `session` entry and the change appears in the feed. |
| Defaults | A `SessionQuery` of `None` lists the owner's sessions, most recently updated first. |

### 6. Performance rules

| Rule | Requirement |
|------|-------------|
| List and get | Cost is independent of transcript length: they read summaries and the session's settings, never entries. |
| Entry pages | Cost is proportional to `limit`, not to the session's length. |
| Follow | Memory per follower is bounded by a configured queue size; an event carries a delta (an entry or a summary), never a whole transcript. |
| Read path | The store constructs models from its own output without re-validating them; validation happens at write. |
| No blocking | Every store call is asynchronous, or a blocking driver runs in a worker thread. |
| Budgets | The benchmark suite measures list, get, entry-page, append, and follow latency and memory, and fails on regression against a committed baseline. The numeric budgets are set by the owner after the first baseline. |

### 7. Dummies and the conformance suite

The package ships `InMemoryStore` (complete, transactional by construction) and a SQLite store (extra). `tether.testing.conformance.session_store_suite(factory)` runs, as asynchronous tests: atomic commit (a failed append writes nothing), summary equals the rebuilt projection, dense positions, idempotent keys, lease exclusivity across tasks (and across processes for a store that supports them), lost-lease refusal, idempotent recovery, owner scoping, keyset paging stability under concurrent writes, change-feed order, resume and resync, coalescing, backpressure isolation, and that no operation reads entries during a list.

## Precedence and Compatibility

- Versioning: one integer, `SESSION_CONTRACT_VERSION`. Stored entries carry no version; a new entry kind is compatible, and a reader MUST ignore an entry kind it does not know (it is passed through as unknown).
- Ordering or precedence: this contract governs persistence and the client API; CONTRACT-002 governs which entries the engine writes and when.
- Backward-compatibility rules: adding an optional field, an entry kind, or a `Change` variant that a follower may ignore is compatible. Changing a signature, a rule, or a cursor format is a new version. A cursor format change invalidates outstanding cursors, which a store refuses with `InvalidCursor`.
- Deprecation rules: a removed item is announced one version ahead and kept, with a warning, for that version.

## Error Semantics

| Condition | Outcome | Retry | Recovery Expectation |
|-----------|---------|-------|----------------------|
| `UnknownSession` (missing or not the owner's) | Raised with no distinguishing detail | No | The caller answers as for a missing session |
| `TurnInProgress` | Raised by `turn` and `send` | Yes, later | One turn per session at a time |
| `SessionClosed` | Raised by `turn` and `send` | No | Create a session |
| `LeaseLost` | Raised by `append`; nothing written | No | Recovery resolves the turn |
| `InvalidCursor` | Raised by `list` | No | List again from the first page |
| `Unsupported` | Raised for a query feature the store lacks (such as `text`) | No | Use a different filter |
| A follower falls behind | `Lagged`, then `ResyncRequired` if its position left the window | Yes | List again, then follow from the new position |
| A store commit fails | The append raises `StoreError` and writes nothing | The engine does not retry a failed commit | The engine stops the turn; `TurnHandle.result()` raises `StoreError`; if the store stays unavailable the lease expires and recovery marks the turn interrupted |
| The process dies mid-turn | The lease expires; `recover` appends `turn_ended: interrupted` | n/a | The consumer sees the session as interrupted |

## Examples

```python
async with Tether(engine=engine, store=InMemoryStore()) as tether:
    client = tether.client("maya")
    session = await client.create(title="demo")
    handle = await client.send(session.id, "look up the sample")
    async for change in client.follow(session_id=session.id):
        if isinstance(change, ReplyText):
            print(change.text, end="")
        elif isinstance(change, SessionUpdated) and change.summary.status == "idle":
            break
    result = await handle.result()
    page = await client.list(SessionQuery(owner="maya", status=frozenset({"idle"})), limit=20)
```

```python
# A browser reconnects: it keeps the last seq it saw
async for change in client.follow(since=last_seq):
    if isinstance(change, ResyncRequired):
        page = await client.list()                    # list again, then follow from the newest last_seq
```

## Non-Normative Notes

- Position-addressed entries and keyset-addressed sessions are chosen so no read needs a scan. A UI that wants "jump to page N" of sessions must search or filter instead.
- A server adapter (HTTP, WebSocket, server-sent events) maps `Client` calls to routes and `follow` to a stream. Its authentication and the choice of `owner` are its own.
- A consumer that needs a turn to be visible only when it completes supplies a store adapter that stages a turn's entries in memory and commits them at the end; the engine's contract does not change (ADR-005).
- The summary `preview` and `text` search reveal message content to a list reader. A deployment that must not do that turns the preview off.

## Open Items

| Item | Owner |
|------|-------|
| The retained window for change records and the default follower queue size | Erik LaBianca, from the first benchmarks |
| Whether `Client.list` should accept an `as_of` position for stable paging under heavy writes beyond the keyset guarantee | Erik LaBianca |
| Fork and rewind (PRD FR-15) entry and API shape | Erik LaBianca, when FR-15 is scheduled |
