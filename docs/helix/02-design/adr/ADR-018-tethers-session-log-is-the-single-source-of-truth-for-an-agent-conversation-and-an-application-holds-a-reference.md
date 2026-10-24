---
ddx:
  id: ADR-018
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
    - id: ADR-003
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: ADR-017
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
    - id: architecture
      kind: informs
    - id: prd
      kind: informs
---

# ADR-018: Execution Order, Authoritative History, and Model Context Have Separate Owners

## Problem

A desktop-style session UI needs the original conversation, while a model needs only a bounded projection. A platform gateway can already retain actual model exchanges; copying those payloads into the execution store creates another content authority. Conversely, inference tables never receive every user message, tool result, approval or recovery fact. They cannot by themselves establish the full conversation or whether an effect happened.

## Decision

We will make authoritative history a replaceable interface, defaulting to local JSONL, with a Gateway-backed provider for externally recorded model exchanges. Tether retains a separate execution journal and derives model context from referenced history under CONTRACT-004/005. An application keeps its session reference and its own business records.

| State | Authority | Surface |
|-------|-----------|---------|
| Session identity, ordering, settings, intents, approvals, leases, resolutions, accepted-message refs | Tether execution journal | CONTRACT-003 |
| Original human/tool content, summaries and actual model request/response content | Selected HistoryProvider; JSONL by default, Gateway-backed composition when selected | CONTRACT-005 |
| Model context, SDK Conversation/ProcessHistory view, caches and compacted projection | Derived, versioned view; never another transcript authority | CONTRACT-004 |
| Business users, entitlements, artifacts and other application records | Application | Capabilities/policy; outside core session types |

- Journal message entries retain source refs, digests and call manifests, not a mandatory full duplicate of content. The journal decides which content was accepted and in what order; the selected binding decides its bytes.
- Gateway exchange history is authoritative for the exact request and response it logged. That request is a model projection, not the original human conversation. Locally denied/unsent content and omitted tool outputs use the provider's explicit writable supplement.
- Current responses may be used in a bounded in-process working set. Pending external history remains pending after restart; it does not authorize another effect or get reconstructed from a later prompt. A policy requiring independent retention selects an explicit mirror.
- JSONL is the default content provider; a paired single-writer JSONL journal supports local reference-client restart without a database server. Postgres remains the multiprocess journal option (ADR-020).
- `create(ref=...)` is idempotent per owner. `send(request_id=...)` compares the canonical accepted input including text, attachments and requested spec, not text alone. The application does not copy the canonical transcript into its own tables.
- Source codecs, provider bindings and correlation are versioned. A history migration appends new verified bindings and preserves the original source identity. Unknown content remains an explicit hole, not an empty message.

## Why We Chose This

### Over requiring all conversation content in the runtime journal

It couples content retention to execution storage and duplicates a platform's actual exchange log. Separate ports permit JSONL locally and external authority in a platform while retaining precise recovery facts.

### Over treating gateway rows as the entire session

Gateway requests repeat old context and may contain summaries/redactions. External logging has delayed, missing and duplicate records, and never sees local-only interaction. Ordered refs and a writable supplement preserve these meanings.

### Over framework-owned message persistence

A selected reusable runner may supply transient conversation state, usage and deferred-input metadata. Its history conversion cannot become a second canonical transcript or replace Tether's effect/approval record.

## Consequences

**Good**: One authority per fact, local file-backed history, replaceable gateway content, and independently inspectable session state/model context.

**Trade-offs**: Two persistence boundaries need explicit content-before-ref ordering; external history cannot guarantee offline availability. The supplement stores events a gateway never saw. A UI must handle partial history while keeping metadata usable.

**Drift signals to watch**: Full shadow copies stored by default; a compacted prompt presented as the original conversation; a new model call inferred from missing history; pending/denied content rendered as empty; a framework persisting another transcript.

## Concern Impact

- **No concern override**: Applies existing hexagonal architecture, resilience, privacy and explicit identity. New history interfaces carry their own dummies and conformance suites.
