---
ddx:
  id: US-002
  type: user-stories
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: derived_from
---

# US-002: Run caller-directed work under session authority

**PRD Requirements**: FR-54, FR-55, FR-59 · **Priority**: P1

## Story

**As a** Maya, Python application developer, **I want** to prepare accepted input and schedule explicit session work, **So that** application work uses the same current caller and lease boundaries as model-driven work.

## Context

This story derives from the accepted consumer requirements in the PRD; no separate feature specification exists for this slice. These operations form the application-directed branch of the combined consumer demonstration. Each has an independent acceptance criterion; their common journey proves that none bypasses session admission or substitutes application identity. The story makes existing accepted behavior traceable; it adds no new runtime scope or release claim.

## Walkthrough

1. Maya submits fabricated input and waits for preparation.
2. Maya invokes a status tool through the same session.
3. Maya schedules a tool-free auxiliary suggestion under her caller identity.
4. The session retains separate operation provenance and purpose-specific usage.

## Acceptance Criteria

- [x] **US-002-AC1** — Given accepted input, when bounded preparation returns ready, then the original input is retained and model execution may begin. See CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004.
- [x] **US-002-AC2** — Given refused or cancelled preparation, when the turn completes, then no model request is sent. See CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004.
- [x] **US-002-AC3** — Given a caller-directed non-approval tool action, when it is invoked and retried, then one source-bound invocation executes without an answer-model call. See CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004.
- [x] **US-002-AC4** — Given a scheduled auxiliary suggestion, when the caller credential is revoked before retry, then no second physical request is sent. See CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004.

## Edge Cases

- Changed caller authority or incompatible source binding refuses before further execution.
- Unknown storage acknowledgment retains uncertainty until current-authorized reopen or reconciliation.
- Unsupported profiles and exhausted finite bounds refuse explicitly.

## Test Scenarios

| Scenario | AC ID | Fabricated state | Expected outcome |
|---|---|---|---|
| 1 | US-002-AC1 | Fabricated count 3, accepted restriction 1; selected durable session | One preparation for an equal request |
| 2 | US-002-AC2 | Fabricated count 3, accepted restriction 1; selected durable session | Zero model calls |
| 3 | US-002-AC3 | Fabricated count 3, accepted restriction 1; selected durable session | One invocation, no model provenance |
| 4 | US-002-AC4 | Fabricated count 3, accepted restriction 1; selected durable session | No identity fallback or second egress |

## Dependencies

- PRD: FR-54, FR-55, FR-59; [[SD-001]]; CONTRACT-001, CONTRACT-002, CONTRACT-003, CONTRACT-004 define exact surfaces.
- Selected PostgreSQL or JSONL profile where durability is required. Application settlement semantics remain application-owned.

## Out of Scope

Consumer deployment, private dependency qualification, new dependency ranges, model service qualification and release publication.
