---
ddx:
  id: ADR-019
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-004
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
    - id: architecture
      kind: informs
    - id: prd
      kind: informs
---

# ADR-019: Session Storage Is Two Ports, a Live Store and an Archive

## Problem

Session storage has two different jobs. The live job is hot and transactional: atomic per-step appends, leases with fencing, summaries kept current, a change feed, pending approvals, and recovery. The archive job is cold and read-mostly: a record of sessions that are finished, kept long and cheaply, read occasionally. One port that serves both makes the live path carry the archive's concerns and the archive carry the live path's cost. A platform adds a third consideration. Where a model gateway logs every request and response, as the Databricks AI Gateway does in inference tables, much of a session's content and usage can be read back from that log instead of being stored twice; the first downstream platform keeps only keys and facts, and reads the payloads on demand, bounded and as the calling user. The operator's direction (Erik LaBianca, 2026-10-22) is pluggable session storage with more than one interface: an archive and a live store.

## Decision

We will define session storage as two ports: a live store, which is authoritative for in-flight and recent sessions, and an archive, which is an optional, separate record of finished sessions. Tether ships default implementations of each (ADR-020).

- **The live store** (CONTRACT-003 section 3) is the port the engine writes through and the client API reads. A turn depends on it and on nothing else.
- **The archive** (CONTRACT-003 section 4) has two optional halves. An `ArchiveSink` takes a closed or idle session out of the live store; an `ArchiveSource` lists, gets, and pages entries for sessions that are not live, and, where it can, returns the request and response of a model call by the provider's request id.
- **Two kinds of archive, told apart by declared capability.** An *authoritative* archive is written by tether, is lossless, and may be *restorable* to the live store. A *derived* archive reconstructs what it can from an external log (a gateway's inference tables), is read-only, and is *partial*: it has content and usage, and it has no tool executions, approvals, or intents, which only tether records.
- **A turn never waits on or fails because of the archive.** The sink runs after a commit and its failures are audited, never raised into a turn.
- **Archive reads are bounded and made as the caller.** They are asynchronous, time-limited, and use the calling actor's credential (ADR-011), as the first downstream platform's per-user log reader does. A read that is pending or denied is reported as pending or denied, never as empty.
- **The client API names the tier.** A summary carries `tier` (`live` or `archive`); `list`, `get`, and `entries` take an `include` choice; cursors belong to a tier; no merged ordering is promised. An archived session is read-only, and `restore` is offered only by a restorable archive.
- **Lifecycle is an application setting.** When a session is archived and when it is evicted from the live store are policy choices with no default proposed; the owner supplies any numbers.
- **Open design, deferred to the Databricks profile's slice.** Whether a live store may hold a model call's content by reference only (resolved from a derived archive when read), so that the gateway's log is the only copy. Version 1 live stores hold content.

## Why We Chose This

### Over one store port
It is simpler to state, and it forces every live store to implement the archive's job and every archive to meet the live path's transaction and latency rules.

### Over a live store with an export tool
It handles authoritative archiving and nothing else; a derived, read-only archive that tether never wrote does not fit an export.

### Over deriving everything from the gateway's log
A gateway log lacks what only tether knows (tool executions, approvals, intents, leases), cannot be written to, and is purged on the gateway's schedule. It is a source for content and usage, not a replacement for the live record.

## Consequences

**Good**: A deployment that logs payloads in its gateway does not store them twice. Cheap long-term retention is possible without touching the live path. The live store stays small and fast. A derived archive and an authoritative one satisfy the same port.

**Trade-offs**: Two ports and two conformance suites. A list across tiers is two calls. A derived archive is partial, and the documents must say so wherever it is shown. Restoring from a derived archive is not possible.

**Drift signals to watch**: A turn that waits on the archive. An archive failure raised into a turn. A derived archive presented as complete. A live-store feature that exists only to serve an archive. A merged-ordering promise across tiers.

## Concern Impact

- **No concern impact**: This applies the `hexagonal-architecture` concern (a port per role).
