---
ddx:
  id: US-001
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

# US-001: Settle a staged tool result

**PRD Requirements**: FR-56, FR-61 · **Priority**: P1

## Story

**As a** Sam, platform engineer, **I want** to settle a staged application change together with its accepted tool result, **So that** a failed or interrupted turn never reports an unproven application outcome.

## Context

This story derives from the accepted consumer requirements in the PRD; no separate feature specification exists for this slice. Atomic content and settlement are combined because the same staged-result journey must preserve its content references and application decision under one selected transaction profile. The story makes existing accepted behavior traceable; it adds no new runtime scope or release claim.

## Walkthrough

1. Sam stages a fabricated count change through a tool.
2. Tether applies current guards and binds the settlement decision.
3. Sam retries or reconciles after an interruption.
4. The session shows a proven result or retains explicit uncertainty.

## Acceptance Criteria

- [x] **US-001-AC1** — Given a staged fabricated write, when its result passes the guards, then the selected transaction profile publishes only a proven settlement outcome. See CONTRACT-002, CONTRACT-003, CONTRACT-005.
- [x] **US-001-AC2** — Given a withheld result, when settlement runs, then the staged write is rolled back. See CONTRACT-002, CONTRACT-003, CONTRACT-005.
- [x] **US-001-AC3** — Given an interrupted independent settlement, when the same decision is reconciled, then the original identity is retained without a duplicate application change. See CONTRACT-002, CONTRACT-003, CONTRACT-005.
- [x] **US-001-AC4** — Given a lost shared-transaction acknowledgment, when the operation is looked up after reopen, then content and all accepted journal projections agree. See CONTRACT-002, CONTRACT-003, CONTRACT-005.

## Edge Cases

- Changed caller authority or incompatible source binding refuses before further execution.
- Unknown storage acknowledgment retains uncertainty until current-authorized reopen or reconciliation.
- Unsupported profiles and exhausted finite bounds refuse explicitly.

## Test Scenarios

| Scenario | AC ID | Fabricated state | Expected outcome |
|---|---|---|---|
| 1 | US-001-AC1 | Fabricated count 3, accepted restriction 1; selected durable session | Accepted result and one application row |
| 2 | US-001-AC2 | Fabricated count 3, accepted restriction 1; selected durable session | No accepted application change |
| 3 | US-001-AC3 | Fabricated count 3, accepted restriction 1; selected durable session | One row after retry |
| 4 | US-001-AC4 | Fabricated count 3, accepted restriction 1; selected durable session | One complete batch or no batch |

## Dependencies

- PRD: FR-56, FR-61; [[SD-001]]; CONTRACT-002, CONTRACT-003, CONTRACT-005 define exact surfaces.
- Selected PostgreSQL or JSONL profile where durability is required. Application settlement semantics remain application-owned.

## Out of Scope

Consumer deployment, private dependency qualification, new dependency ranges, model service qualification and release publication.
