---
ddx:
  id: FEAT-017
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
    - id: US-069
      kind: informs
    - id: ADR-022
      kind: informed_by
---

# Feature Specification: FEAT-017 — Interface Distribution

**Feature ID**: FEAT-017
**Priority**: P2
**Owner**: Erik LaBianca
**Covered PRD Subsystem(s)**: Interface Distribution and Compatibility
**Covered PRD Requirements**: FR-70; NFR-9
**Cross-Subsystem Rationale**: None — installed artifact independence is the outcome.

## Overview

One source repository must produce independently consumable artifacts. An
embedder in another repository uses supported released packages without Tether
source paths, hidden assets or mandatory optional interface dependencies.

## Ideal Future State

An embedder installs the desired layers, adds its own tools/policy/UI, and deploys
a supported carrier from a clean checkout. Its compatibility matrix is explicit.

## Problem Statement

- The existing project builds one Python distribution and has no browser packages.
- Checkout-local imports and untested asset assembly can conceal release defects.
- Desired outcome: installed Python/npm artifacts reproduce the approved journeys
  without access to the source monorepo.

## Requirements

- **DIST-01** — Core, optional HTTP, UI hosting and carrier artifacts must declare
  separate dependencies and avoid overlapping Python file ownership.
- **DIST-02** — Browser client, React hooks and UI must export typed public entry
  points with enforced dependency direction, peer dependencies and styles opt-in.
- **DIST-03** — Each artifact must include required types, assets, licenses and
  version/compatibility metadata; browser assets and wire schemas must correspond.
- **DIST-04** — A clean external embedder fixture must exercise direct client use,
  replacement API/UI and the default carrier using installed artifacts only.
- **DIST-05** — A complete source-specific release gate must include regressions,
  typing, import negatives, substitution, browser proof and actual platform
  qualification for each advertised profile. Local mocks cannot qualify Apps.
- **DIST-06** — Public guides must distinguish supported versions/subsets,
  unsupported capabilities and remaining platform gates. Publishing/deployment
  remains a separate authorized action from preparing the release packet.

### Non-Functional Requirements

NFR-4/5/8/9 apply. No undeclared dependency, missing artifact or untested version
range may pass the installed-package gate. Repeated builds must produce matching
semantic assets/manifests under the pinned toolchain; timestamps are not proof.

## User Stories

- [US-069 — Adopt released interface packages](../user-stories/US-069-independent-package-adoption.md).

## Edge Cases and Error Handling

- Mismatched wire/client majors fail capability negotiation before commands.
- Missing UI assets fail readiness rather than serving an empty SPA.
- Missing optional extras produce actionable construction errors.
- An unqualified Apps/Lakebase profile remains explicitly unqualified at release.

## Constraints and Assumptions

ADR-022 owns proposed package names/layout. Registry availability and exact
version ranges require bounded checks. Distribution separation does not require
separate repositories or uncoordinated releases.

## Dependencies

FEAT-013–016, ADR-022, CONTRACT-006/007, TP-002 and IP-002.

## Out of Scope

Moving embedder business code into Tether, forced independent version numbers,
automatic publication or creating production resources during application startup.
