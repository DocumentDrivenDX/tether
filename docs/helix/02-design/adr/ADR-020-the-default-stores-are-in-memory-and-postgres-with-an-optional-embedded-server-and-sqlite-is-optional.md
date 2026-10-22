---
ddx:
  id: ADR-020
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-005
      kind: informed_by
    - id: ADR-009
      kind: informed_by
    - id: ADR-019
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
    - id: architecture
      kind: informs
    - id: prd
      kind: informs
---

# ADR-020: The Default Stores Are In-Memory and Postgres, With an Optional Embedded Server, and SQLite Is Optional

## Problem

Tether should ship one or two default session stores, starting with an in-memory one, and the choice of the first durable store was open (operator direction, Erik LaBianca, 2026-10-22: perhaps a base Postgres implementation with an optional embedded server, possibly SQLite). The choice has to serve three needs: a clone that runs in seconds, a durable store for the crash-recovery guarantees (ADR-005, ADR-010), and the first downstream platform, whose session store is Postgres (Lakebase). That platform's Postgres backend, its pooled connection and embedded-server handling, and its turn-event store are tested code that can be ported.

## Decision

We will ship an in-memory live store and a Postgres live store as the defaults, with an optional embedded Postgres for local use, and treat SQLite as an optional third store built later if it is needed.

- **In-memory (live and archive).** Complete and transactional by construction, declared `durability=none` (ADR-005). It is the default for the demo, the tests, and the benchmarks that need no I/O.
- **Postgres live store.** `tether.stores.postgres`, in the `postgres` extra (`psycopg` with its pool, used through its asynchronous API), ported from the first downstream platform's backend. Only this module imports `psycopg`.
- **The lease** is a row holding an owner token and a heartbeat, checked in the same transaction as each append (fencing). The first downstream platform used a session-level advisory lock, which is released the moment a connection dies but holds one connection per running turn; the slice evaluates both with the benchmarks and records the choice.
- **Cross-process followers** use Postgres `LISTEN` and `NOTIFY` where available, and polling as the fallback; the polling interval is a setting the owner supplies.
- **Migrations** are tether's own small versioned SQL runner with a schema-version table. No object-relational mapper and no migration framework is a dependency.
- **Embedded server.** The `pgserver` extra pulls the `pgserver-pg18` build (PostgreSQL 18, DocumentDrivenDX's). `PostgresStore.embedded(path)` starts and stops it and is the only code that imports `pgserver`. A laptop then runs the same SQL as a deployed Postgres.
- **The quickstart.** The demo's default store is in memory and needs no setup. The durable demo (kill the process, restart, resume) uses the embedded server. If starting the embedded server exceeds the quickstart budget (PRD, 10 s on a warm cache), SQLite becomes the durable demo's store.
- **SQLite is optional.** If built, it uses the standard library in worker threads (ADR-009). It is not on the path to 0.1.
- **Versions.** PostgreSQL 18 is the tested version, because it is the embedded build; other versions are untested until the conformance matrix covers them.
- **Archive defaults.** An in-memory archive ships for tests. A derived archive that reads a gateway's inference tables ships with the Databricks profile (ADR-013), ported from the first downstream platform's gateway-log reader.

## Why We Chose This

### Over SQLite first
It needs no dependency and starts instantly, which suits the quickstart. It also has nothing to port from the first consumer, weaker cross-process behavior, and no `NOTIFY`. It remains the fallback for the demo.

### Over Postgres only, with no in-memory store
A clone would need a server or an embedded build before anything runs, which defeats clone-and-try.

### Over a file or JSONL store
It is simple, and it has no transactions, no indexes for summaries, and no lease, so it cannot meet the store contract.

## Consequences

**Good**: A durable store that matches the first consumer's and ports tested code. Real multi-process leases and notifications. A zero-setup demo. The same SQL on a laptop and in production.

**Trade-offs**: Two optional dependencies (`psycopg` and `pgserver`) and a wheel whose size and first-start time are not measured yet. Postgres 18 is the only tested version. The quickstart's durable path depends on the embedded server's start time, which the slice measures; the SQLite fallback is the mitigation.

**Drift signals to watch**: An import of `psycopg` or `pgserver` outside the Postgres module. An ORM or migration framework added. A store whose conformance suite is skipped. The durable demo exceeding its budget without the fallback being taken.

## Concern Impact

- **No concern impact**: `python-uv` covers the stack; this ADR is the coverage AGENTS.md requires for `psycopg` and `pgserver` as extras.
