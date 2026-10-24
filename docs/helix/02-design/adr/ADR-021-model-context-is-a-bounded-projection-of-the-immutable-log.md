---
ddx:
  id: ADR-021
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: component-profile-hermes-agent
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: CONTRACT-004
      kind: informs
    - id: architecture
      kind: informs
    - id: SD-001
      kind: informs
---

# ADR-021: Model Context Is a Bounded Projection of the Immutable Log

## Problem

Long conversations need bounded model input, recoverable summaries, and clear omissions. The narrowing provider hook cannot introduce summaries or select an independently reconstructed context view. Letting a provider replace arbitrary messages would also let it invent tool calls, bypass policy through auxiliary model calls, or turn a lossy summary into execution state. Hermes's context separation, reload markers and cache trade-offs expose useful mechanisms, while Tether's append-only log remains authoritative.

## Decision

We will include explicit context projection and compaction contracts in the initial design and first release. One optional strategy plans source-addressed selections; the engine validates and materializes them, executes bounded auxiliary summarization through its own model/policy ports, and records provenance before use. CONTRACT-004 owns the exact surface.

- The engine takes explicit model limits and an estimator. Every main or auxiliary request reserves output space and accounts for its full payload. Missing configuration or an input that cannot fit ends explicitly before the request.
- Context selection and persisted history are separate. Plans reference original source positions, existing summaries, proposed summaries or explicit omissions. Originals never change. Pending approvals, uncertain effects and invocation identity are always derived from the authoritative log.
- `before_request` remains a narrowing/redaction hook. It runs before context planning; summaries use only the currently authorized, filtered view. A new context strategy cannot remove protection or introduce executable calls.
- Summary calls use current identity, policy, usage accounting, deadlines and profiles, with no tools or recursive compression. Failures preserve source content and stop explicitly.
- Built-ins provide full context, deterministic recent-context selection, and opt-in summarization. User instructions stay verbatim by default. The demo needs no auxiliary model or network.
- Prompt and tool ordering are deterministic. Cache economics can inform strategy configuration but never override current definitions, authorization, source compatibility or budget checks.

**Provisional risk**: Token estimates can differ from provider accounting, and summaries are lossy. No server acceptance or semantic fidelity is assumed. Configured margins, explicit quality labels, source references and conformance/fidelity tests bound the claims; strategies and profiles remain replaceable.

## Why We Chose This

### Over broadening the provider hook to arbitrary message replacement

That combines redaction, context selection, content creation and execution authority in one weakly constrained output. Source-addressed plans let the engine enforce coverage and call/result integrity while preserving existing provider semantics.

### Over editing or soft-archiving the source transcript during compaction

A mutable active transcript creates another recovery generation and risks losing evidence or loading duplicates. Appended summaries and context-use records preserve one source log and make selection reconstructible after a crash.

### Over autonomous memory and background summary workers

They add identity, lifecycle, scheduling and billing paths. Explicit engine-mediated requests cover the current need and keep those concerns inside the existing gates.

## Consequences

**Good**: Long-session behavior is designed before release, with replaceable strategies, honest omissions, bounded requests, immutable history, and governed summary calls.

**Trade-offs**: Model configuration and estimation are required. Summarization adds latency/cost and cannot guarantee perfect recall. Preserving large protected inputs can lead to an explicit refusal. The first release needs context conformance and recovery evidence.

**Drift signals to watch**: A summary deciding approval/effect state; a strategy dispatching a model/tool; inferred model windows; rewritten live arguments; a truncation marker executed as content; fallback after a failed summary silently discarding history.

## Concern Impact

- **No concern override**: Applies the existing hexagonal architecture, resilience, security, testability and model-provider-independence concerns. ADR-003 selects Agent; this context decision selects no additional Harness capability or independently authoritative persistence system.
