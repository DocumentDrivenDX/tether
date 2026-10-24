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
    - id: CONTRACT-005
      kind: informs
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

# ADR-019: Session Persistence Separates Execution, History, and Optional Archiving

## Problem

The live execution record needs atomic ordering, summaries, approvals, leases and a change feed. Authoritative history needs immutable content and actual model exchange retrieval, possibly from a service Tether does not control. Archiving is a separate retention job. Treating a gateway log only as a cold derived archive prevents it from owning model exchange content while the session is live.

## Decision

We will compose a live execution store, an authoritative HistoryProvider, and an optional archive. CONTRACT-003 governs execution/archiving; CONTRACT-005 governs history and correlation. This replaces the earlier assumption that every live store must contain all message bytes.

- **Execution.** A live store transaction commits journal facts and history refs, materialized metadata, and change records together. Leases fence these writes and never imply control of external effects.
- **History.** JSONL is the default. GatewayHistory uses inference tables for model exchanges plus an explicitly configured writable supplement for application-only content. Reads are bounded, as the current caller, and preserve pending/partial/denied/missing/ambiguous states.
- **Content before reference.** A writable history record reaches its declared durability before the journal references it. Failed journal commits may leave invisible orphan content; they do not create an accepted message. Externally recorded exchanges instead use prepared attempts and explicit pending bindings, without claiming an atomic commit with a gateway.
- **Working context.** Current observed data can support the running turn. Future context/argument reconstruction can depend on authoritative history under a deadline; unavailable content stops that dependent step explicitly. The execution journal still reports unknown effects and pending interaction without payloads.
- **Archive.** An authoritative archive exports journal entries and declares whether it also preserves referenced content. A complete/restorable claim requires verified reference closure; metadata-only exports and historical gateway imports remain partial. Archive export is never in the turn path.
- **Retention.** The application selects archive, mirror/cache and eviction settings. No source or referenced content is silently evicted as part of compaction. An eventual source does not imply indefinite retention or recoverability.
- **Client.** Lists, snapshots, refs, effect state and journal pages stay fast without history reads. Separate position-paged history resolves content and readiness; known model-call detail resolves through the same history interface. Source timestamps do not decide transcript order.

## Why We Chose This

### Over a combined session store

It makes every provider implement transactional execution, content persistence and retention. The interfaces have different durability and ownership constraints, especially for a read-only gateway.

### Over gateway logs as a derived archive only

That duplicates live model payloads and postpones the owner's intended source replacement. First-class history refs let external content be authoritative while local execution remains correct.

### Over deriving all recovery from inference tables

An external table has no atomic intent/approval/resolution record and can omit a model exchange. Correlation identifies observations; it does not prove tool completion or grant access.

## Consequences

**Good**: Replaceable original content, metadata-only execution storage, explicit external holes and independent archiving.

**Trade-offs**: Cross-backend atomicity is limited to the journal; source durability/availability qualifies recovery. Complete offline archival requires an explicit content export, not merely saving references.

**Drift signals to watch**: An archive required to run a turn; an external source declared complete without coverage; a content read that silently changes identity; a reference treated as proof of persistence; JSONL required to implement a multi-host database lease.

## Concern Impact

- **No concern override**: A port per responsibility, with explicit capabilities and conformance.


The selected composition exposes optional independent ArchiveSource/ArchiveSink halves, a bounded
volatile reference archive and an explicit off-turn archive_session helper. Application policy owns
scheduling and eviction; no automatic archive_on driver is selected. Full transfer carries the
existing reference-complete ArchiveBundle plus a metadata projection. Restore preserves original
closure and invocation facts, uses fresh caller authority and rejects conflicting immutable identity.
Cached archival content is never itself a disclosure grant. Cross-provider address remapping remains
unqualified; the implemented profile restores original provider/codec addresses unchanged.
