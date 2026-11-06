---
ddx:
  id: US-065
  type: user-stories
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: FEAT-013
      kind: derived_from
    - id: prd
      kind: derived_from
    - id: SD-002
      kind: informs
---

# US-065: Embed and Share a Client Host

**Feature**: FEAT-013 — Public Client Embedding
**Feature Requirements**: EMB-01–06
**PRD Requirements**: FR-66; NFR-9
**Priority**: P2

## Story

**As a** Sam, platform engineer
**I want** to construct one client host and inject authorized clients into my views
**So that** replacing a view does not require private runtime access.

## Context

The existing TUI assembles a concrete host. This story makes its consuming view
replaceable while retaining Tether's actual-worker, policy and journal authority.
CONTRACT-006 defines the exact public facade.

## Walkthrough

1. Sam installs core and supplies explicit configuration and fabricated ports.
2. Sam constructs a host through the public embedding boundary.
3. Sam binds an actor and gives the client to a terminal consumer and test view.
4. Both views show the same accepted fabricated conversation.
5. Sam closes one view, then explicitly shuts down the host.

## Acceptance Criteria

- [ ] **US-065-AC1** — Given a core-only installation, when Sam constructs and uses
  the supported facade, then it needs no optional UI/server dependencies or private imports.
- [ ] **US-065-AC2** — Given conforming fabricated ports, when Sam injects them,
  then the same client conformance suite succeeds without real providers.
- [ ] **US-065-AC3** — Given an injected client, when the terminal view starts,
  then it creates no second runtime or storage authority.
- [ ] **US-065-AC4** — Given two borrowed consumers, when one closes, then the
  other retains access and accepted work is not canceled by that closure.
- [ ] **US-065-AC5** — Given an actual worker still running, when host shutdown
  reaches its bound, then ownership remains retained until physical completion.
- [ ] **US-065-AC6** — Given a retained actor-bound client, when policy is revoked,
  then subsequent disclosure and execution refuse under current policy.
- [ ] **US-065-AC7** — Given a supported remote client, when the terminal uses it,
  then the declared consumer conformance cases match embedded behavior.
- [ ] **US-065-AC8** — Given an existing supported composition example, when the
  new facade is installed, then its documented compatibility path remains valid.

## Edge Cases

- Construction failure releases owned resources but preserves borrowed providers.
- A canceled wait does not imply command cancellation or known failure.
- An unsupported capability refuses explicitly without constructing a local fallback.

## Test Scenarios

| Scenario | AC ID | Input / State | Action | Expected Result |
|---|---|---|---|---|
| Clean embed | US-065-AC1 | Empty isolated Python environment | Install core and run fabricated example | No optional imports |
| Borrowed view | US-065-AC4 | Two clients, one accepted turn | Close first view | Second follows completion |
| Worker close | US-065-AC5 | Barrier-held synchronous worker | Close host at bound | Ownership stays held |
| Revocation | US-065-AC6 | Read allowed then revoked | Read retained session | Explicit refusal |

## Dependencies

FEAT-013; EMB-01–06; FR-66/NFR-9; CONTRACT-003/006. Remote parity also depends on
US-066's HTTP/remote implementation. TP-002 allocates tests; IP-002 orders work.

## Out of Scope

Changing engine semantics, replacing the single runner or granting a view direct
store access.
