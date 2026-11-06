---
ddx:
  id: FEAT-013
  type: feature-specification
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: derived_from
    - id: US-065
      kind: informs
    - id: CONTRACT-006
      kind: informs
    - id: SD-002
      kind: informs
---

# Feature Specification: FEAT-013 — Public Client Embedding

**Feature ID**: FEAT-013
**Priority**: P2
**Owner**: Erik LaBianca
**Covered PRD Subsystem(s)**: Client API
**Covered PRD Requirements**: FR-66; NFR-9
**Cross-Subsystem Rationale**: None — one public embedding capability.

## Overview

A Python application must configure, construct, use and close Tether through a
supported public client boundary. It can pass that client to a terminal or web
consumer without exporting runtime internals or transferring lifetime ownership.

## Ideal Future State

An embedder can use installed artifacts to host one authorized session service,
inject a client into multiple views, substitute a conforming implementation,
and shut down predictably. Replacing a view does not change execution authority.

## Problem Statement

- Current construction exposes concrete engine/store/history composition; the
  reference TUI also constructs its own host.
- Embedders otherwise need private imports or duplicated assembly/lifetime code.
- Desired outcome: ordinary client use and supported port injection require no
  private imports, with substitution and owned/borrowed lifetime proofs.

## Requirements

- **EMB-01** — The boundary must provide typed configuration, factories, public
  domain/error types and lifecycle operations without framework/provider types.
- **EMB-02** — An embedder must inject supported model/history/store/policy ports
  through public contracts; imports and defaults must perform no hidden I/O.
- **EMB-03** — The terminal consumer must accept an injected local or remote
  client. Its standalone launcher explicitly owns its client host.
- **EMB-04** — Closing a borrowed consumer must preserve the host and accepted
  work. Closing an owned host must seal admission and accurately report pending
  physical workers rather than release ownership early.
- **EMB-05** — Actor-bound clients must preserve current policy/credential checks,
  source verification, lease serialization and unknown-effect barriers.
- **EMB-06** — Existing public composition imports must have a tested compatibility
  path; changing facade construction cannot silently change defaults/semantics.

### Non-Functional Requirements

NFR-1/2/3/5/6/9 apply. Every supported operation has a conformance case; every
forbidden consumer-to-private import has a negative control. No terminal/web
framework is required for a core-only install.

## User Stories

- [US-065 — Embed and share a client host](../user-stories/US-065-public-client-host.md).

## Edge Cases and Error Handling

- Incomplete shutdown retains ownership and exposes completion state.
- Actor revocation refuses disclosure or dispatch through a retained client.
- Remote capability absence is explicit; it never falls back to a broader client.
- Failed construction closes owned resources without closing borrowed ports.

## Constraints and Assumptions

CONTRACT-006 owns exact facade, capability and lifecycle interfaces. Existing
CONTRACT-001–005 semantics remain authoritative. A facade is not a second runner.

## Dependencies

PRD FR-66/NFR-9, ADR-004/022, CONTRACT-003/006, SD-002 and TP-002.

## Out of Scope

Application business tools, mandatory server dependencies and a dependency
injection container or general plugin discovery mechanism.
