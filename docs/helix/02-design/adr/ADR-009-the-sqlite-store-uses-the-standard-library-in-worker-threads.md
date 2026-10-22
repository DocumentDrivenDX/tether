---
ddx:
  id: ADR-009
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
---

# ADR-009: The SQLite Store Uses the Standard Library in Worker Threads

## Problem

SQLite is a file database with no server, so a store built on it could run from a clone without setup (PRD FR-16, FR-33). It is an optional store (ADR-020); this ADR decides how it is built if it is. The library is asynchronous (ADR-001) and the `sqlite3` module is blocking. The store must give atomic per-step commits (ADR-005), cheap list and page reads (ADR-004), and cross-process leases, without adding a dependency to the core's path.

## Decision

We will implement `SqliteStore` on the standard library's `sqlite3`, running every blocking call in a bounded set of worker threads, with no `aiosqlite` or other wrapper.

- **One writer thread.** All writes go through one thread holding one connection, which serializes commits (SQLite permits one writer) and keeps each append's entries, summary update, and change records in one transaction.
- **Reader threads.** Reads use a small pool of read connections; in write-ahead-log (WAL) mode readers do not block the writer.
- **Durability by default.** `synchronous=FULL`, so a commit that returned survives a crash of the operating system; a setting selects `NORMAL` for speed at the cost of that guarantee.
- **Leases in the file.** A lease is a row with an owner token and a heartbeat time; acquiring one is a conditional update in a transaction, so it holds across processes that share the file.
- **Cross-process followers poll.** A follower in another process reads change records after its last sequence at a configured interval; in-process followers use the in-process change bus without polling. The default interval is a proposed 250 ms, to be set by the benchmarks.
- **The schema is private.** Only the store knows it; a version number in the file lets a later release migrate it.

## Why We Chose This

### Over `aiosqlite`
It presents an asynchronous interface by running the same blocking calls in a thread of its own, so it adds a dependency without changing the mechanism, and it hides the writer-thread design this store needs.

### Over a server database first
It would need setup before a clone could be tried and would put a server in the quickstart; a server store is an adapter for later (PRD parking lot).

### Over a store that rewrites a JSON document per session
It is simpler, but each step rewrites the whole session and lists cannot be indexed (ADR-005).

## Consequences

**Good**: No new dependency. A working durable store from the first clone. Real transactions and indexes for summaries and keyset paging.

**Trade-offs**: With `synchronous=NORMAL` an operating-system crash can lose the last commits, including an intent entry (ADR-014), so a call may be unrecorded; the store then declares `durability=process` (ADR-005). Thread and connection management is the store's to get right, and the conformance suite and the cross-process lease test are the check. Followers in other processes see changes after a polling delay. SQLite on a network file system is unsupported.

**Drift signals to watch**: A blocking call on the event loop. More than one writer connection. A schema read by anything outside the store. A commit that writes entries and the summary separately.

## Concern Impact

- **No concern impact**: The decision uses no new third-party dependency.
