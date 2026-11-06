---
ddx:
  id: US-003
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

# US-003: Evolve session tools with explicit approval

**PRD Requirements**: FR-57, FR-58, FR-60 · **Priority**: P1

## Story

**As a** Maya, Python application developer, **I want** to publish updated tools and continue with an explicitly edited approval, **So that** the next operation uses accepted definitions while already admitted work keeps its original binding.

## Context

This story derives from the accepted consumer requirements in the PRD; no separate feature specification exists for this slice. Publication, transition and edited approval are combined to exercise definition evolution across one approval journey. Separate criteria retain independent observable outcomes and reject mid-run authority changes. The story makes existing accepted behavior traceable; it adds no new runtime scope or release claim.

## Walkthrough

1. Maya receives a fabricated proposal to add three items.
2. Maya approves a tool-defined restriction to one item.
3. Maya publishes the next complete tool generation after the operation.
4. Maya transitions the specification and starts the next operation.

## Acceptance Criteria

- [x] **US-003-AC1** — Given an active invocation, when a complete new tool generation is published, then the invocation retains its admitted declaration through actual completion. See CONTRACT-002, CONTRACT-003.
- [x] **US-003-AC2** — Given a completed call group, when an authorized specification transition is accepted, then the next operation uses the accepted specification. See CONTRACT-002, CONTRACT-003.
- [x] **US-003-AC3** — Given a pending proposal, when an allowed semantic restriction is approved, then the original, shown and executable inputs remain independently identifiable. See CONTRACT-002, CONTRACT-003.
- [x] **US-003-AC4** — Given an accepted edit before intent, when the durable session is reopened, then only the accepted executable arguments may run. See CONTRACT-002, CONTRACT-003.

## Edge Cases

- Changed caller authority or incompatible source binding refuses before further execution.
- Unknown storage acknowledgment retains uncertainty until current-authorized reopen or reconciliation.
- Unsupported profiles and exhausted finite bounds refuse explicitly.

## Test Scenarios

| Scenario | AC ID | Fabricated state | Expected outcome |
|---|---|---|---|
| 1 | US-003-AC1 | Fabricated count 3, accepted restriction 1; selected durable session | Old worker retains original generation |
| 2 | US-003-AC2 | Fabricated count 3, accepted restriction 1; selected durable session | Next request has the new binding |
| 3 | US-003-AC3 | Fabricated count 3, accepted restriction 1; selected durable session | Propose count 3, approve count 1 |
| 4 | US-003-AC4 | Fabricated count 3, accepted restriction 1; selected durable session | One execution using count 1 |

## Dependencies

- PRD: FR-57, FR-58, FR-60; [[SD-001]]; CONTRACT-002, CONTRACT-003 define exact surfaces.
- Selected PostgreSQL or JSONL profile where durability is required. Application settlement semantics remain application-owned.

## Out of Scope

Consumer deployment, private dependency qualification, new dependency ranges, model service qualification and release publication.
