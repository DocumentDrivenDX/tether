---
ddx:
  id: ADR-005
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: ADR-019
      kind: informed_by
    - id: ADR-014
      kind: informed_by
    - id: ADR-010
      kind: informed_by
    - id: product-vision
      kind: informed_by
    - id: competitive-analysis
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
---

# ADR-005: A Session Is an Append-Only Entry Log Committed Per Step

## Problem

When a session's changes become durable decides what a crash costs and what a UI can see. If a turn is persisted only when it ends, a crash loses the whole turn, a UI cannot read a running turn, and the record of what a turn did before it died is missing. Pi Durable commits a checkpoint at each step and resumes from it ([competitive analysis](../../00-discover/competitive-analysis.md)). A user interface that follows live conversations needs committed entries to read.

## Decision

We will store a session as an append-only log of typed entries, and commit each step's entries durably, in one transaction together with the updated summary and the change-feed record, before the next step starts.

- **Entries.** A closed set of typed entries: messages, turn started and ended (with a stop reason), model-call facts (tier, request id, token usage, duration), tool-call facts, confirmation requested and answered, settings changed, and a custom entry a provider may write under its own namespace. Entries are immutable once written. A session's own position and a per-owner sequence are assigned by the store.
- **One commit per step.** A step is a model call with its reply, or a tool call with its result. Its entries are appended in one transaction that also updates the session summary and the change-feed record (ADR-004).
- **A turn holds a lease.** A running turn holds a per-session lease with a heartbeat, and every append checks the lease in the same transaction (fencing). A turn whose lease expires is interrupted: recovery appends an interrupted turn entry, and resume continues it explicitly (ADR-010).
- **Cancellation keeps committed steps.** A cancelled turn leaves the steps already committed, and the log ends with a cancelled turn entry.
- **The log is the source of truth.** Summaries and the change feed are projections of it. A store may rebuild a summary from the log; the conformance suite checks that the projection and the log agree.
- **Persistence is never deferred, and a store declares what it guarantees.** An append returns only after the commit has the durability the store declares: `none` (lost with the process), `process` (survives a process kill), or `os` (survives an operating-system crash). A store also declares whether its leases are in-process or cross-process and whether its commit checks the lease token (fencing). The recovery guarantees of ADR-010 and ADR-014 hold only for a store that declares durable commits and fenced leases, and the documents state them by capability (CONTRACT-003 section 3).
- **Visibility is a read-side policy, and it is deferred.** If a consumer needs a turn to appear only when it completes, the read side hides its entries behind a per-session watermark that advances at the turn's end, including an end that recovery appends. The engine and recovery always read everything, and nothing is held back from the store. Version 1 offers only live visibility. A store that holds a turn's entries in memory until the turn ends is not permitted, because it breaks durable commits, recovery, and the intent entry of ADR-014.

## Why We Chose This

### Over persisting a turn when it ends
It is simple, atomic per turn, and lets a consumer keep a turn invisible until done. It also loses a crashed turn entirely, leaves a UI unable to read a running session, and records nothing of a turn that failed. A watermark on the read side preserves the option for consumers who want it, without deferring persistence.

### Over snapshotting the session as one document
It is easy to store and read, but a growing document makes every step rewrite the whole session, makes paging transcripts awkward, and cannot give a UI a feed of changes.

### Over a framework's checkpointer
It would make the framework the owner of paused state, which the target user's application already owns (ADR-003).

## Consequences

**Good**: A UI reads a running session. A crash loses at most the step in flight. Recovery has a defined starting point. The summary and the feed come from one commit, so they cannot disagree with the log.

**Trade-offs**: A commit per step costs latency; batching one step's entries into one transaction bounds it, and the benchmark suite measures it. Stores must offer transactions and an ordered log. A cancelled or interrupted turn leaves a partial transcript, so a consumer that dislikes partial turns uses the visibility watermark when it exists. Rewrite operations (editing history) are forks, not mutations.

**Drift signals to watch**: An entry updated in place. A summary computed from entries on read. A write path that persists at turn end by default. A store whose summary disagrees with its log. A loop that commits two steps in one transaction.

## Concern Impact

- **No concern impact**: The decision applies `resilience` (idempotent recovery) and the `event-sourcing` idea of a log with projections, without selecting an event-sourcing framework.
