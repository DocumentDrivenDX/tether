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
    - id: CONTRACT-005
      kind: informs
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

# ADR-020: Local JSONL Is the Reference Default; Postgres Supplies Multiprocess Execution Storage

## Problem

The owner wants local JSONL authoritative history with a replaceable AI Gateway provider. A desktop-style local reference client also needs its accepted-session index and recovery journal after restart; durable content behind a volatile session index would strand the history. Multiprocess deployments still need the selected Postgres transaction, lease and notification behavior.

## Decision

We will ship JSONL history as the default and specify a paired single-writer JSONL execution journal for local reference clients. In-memory implementations serve deterministic tests and explicit volatile demos; Postgres serves multiprocess applications. All implement their declared ports and capabilities, not one universal guarantee.

- `JsonlHistory` stores immutable content envelopes and rebuildable keyed indexes (CONTRACT-005). It does not itself implement execution transactions.
- `JsonlSessionStore` implements LiveStore using atomic framed journal batches, materialized in-memory indexes rebuilt at startup, and one operating-system-owned writer per root. It declares process-scoped leases and fencing; it does not advertise multi-host concurrent writers. CONTRACT-003 specifies the local profile.
- Reference clients compose the two local adapters with one root-owner service. No server is required. Their file roots and fsync/durability modes are explicit settings. The configured restart promise holds only when both content and journal meet it.
- `InMemoryStore` and `InMemoryHistory` declare `durability=none`. Their demo uses `resume="off"`; they never claim persistence across process death.
- `PostgresStore` retains atomic facts/refs/summary/change commits, row-token fencing and cross-process leases. It uses asynchronous psycopg; LISTEN/NOTIFY is optional with bounded polling fallback. Migration code and every psycopg import remain in that adapter.
- Optional embedded Postgres uses the qualified PostgreSQL18 binary payload with an explicit asynchronous helper lifetime. Persistent data and runtime paths, bootstrap username and finite caller waits are required configuration. The helper registers borrowed database roots and stops only after their physical work drains. It creates no pool, selects no application role and runs no implicit migration. It is not required by the local JSONL quickstart. The gateway history provider composes with either journal.
- SQLite remains optional if a consumer selects it. No object-relational mapper or migration framework is required by this decision.

The local file profile remains limited to its qualified single-writer filesystem. Shared/network
filesystems and multiple writer processes require separate qualification; Postgres supplies the
selected multiprocess alternative. Neither profile implies power-cut proof.

## Why We Chose This

### Over JSONL content with an in-memory-only session journal

Content could survive but session identity, acceptance/order and recovery state would disappear. A local journal preserves those facts without making raw history a second execution authority.

### Over requiring Postgres before trying the local reference clients

It adds server startup for a file-backed desktop-style use case. Postgres remains valuable for shared deployment behavior and strong multiprocess coordination.

### Over claiming a plain append file is automatically transactional

A journal batch, integrity chain, acknowledged fsync boundary, root lock and rebuild rules are required. The capabilities stay scoped to the tested local profile.

### Over the cached synchronous embedded-server helper

The pinned binary package's get_server caches a server by canonical data directory, counts process
PIDs rather than consumers, and registers cleanup before all startup steps complete. Its public
cleanup surface cannot reliably express canceled partial startup or several borrowed database roots.
Tether therefore owns a small retained subprocess lifetime around the same binary payload. This adds
process and filesystem qualification work, while preserving application-owned acquisition and roles.
The helper refuses orphan servers and unmanaged data rather than adopting or administering them.

## Consequences

**Good**: Default local persistence, no mandatory server, isolated content/history roles, and a separately qualified multiprocess store.

**Trade-offs**: Another LiveStore adapter and its crash tests; startup index rebuild has measurable cost. Local exclusive ownership is narrower than Postgres. External history readiness still qualifies recovered content.

**Drift signals to watch**: A volatile demo advertised as durable; two local writers; a hot list scanning JSONL; file corruption silently repaired; a lease or durability claim exceeding qualification; a mandatory embedded server for the reference quickstart.

## Concern Impact

- **No new dependency**: File I/O and process locks use the standard library off the event loop; existing optional psycopg/embedded-server dependencies retain their adapter boundaries. The storage slot remains a port, not a global database choice.


The Postgres profile selects an injected asynchronous acquisition context, not an implicit DSN,
role switch or internally opened pool. Applications can choose per-user connections/token refresh,
RLS setup or explicit service maintenance. Caller storage uses pure non-owning for_actor facades;
maintenance/migration authority is separate and never a failed-caller fallback. Every connection
gets the present copied actor; SQL still constrains owner/session. Bound facades share root
namespace ownership and cannot close its external pool. A stable non-secret database/schema
identity joins process-local worker ownership across roots; shutdown retains it through actual
exit. Explicit plain-SQL versioned migrations remain adapter-owned.

The inspected consumer backend supplies reusable bounded acquisition, schema validation, short
transactions and separate long-lock resource handling. Its service pools/synchronous wrappers do
not qualify caller-scoped or RLS behavior; those need fresh async implementation and live tests.
