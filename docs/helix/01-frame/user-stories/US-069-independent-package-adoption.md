---
ddx:
  id: US-069
  type: user-stories
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: FEAT-017
      kind: derived_from
    - id: prd
      kind: derived_from
---

# US-069: Adopt Released Interface Packages

**Feature**: FEAT-017 — Interface Distribution
**Feature Requirements**: DIST-01–06
**PRD Requirements**: FR-70; NFR-9
**Priority**: P2

## Story

**As a** Maya, Python application developer
**I want** to install only the Tether interfaces my application needs
**So that** my separate application can evolve without a source-tree dependency.

## Context

Monorepo tests alone cannot prove distribution independence. This story uses
built artifacts from an external fixture directory and keeps optional frameworks
outside the direct embedding dependency closure.

## Walkthrough

1. Maya chooses a documented compatible Python/browser artifact cohort.
2. Maya installs it in a clean separate application fixture.
3. Maya runs direct client use, then composes the supplied API and custom UI.
4. Maya replaces a presentation component and the API provider through public seams.
5. Maya builds a carrier deployment packet and reads its explicit support evidence.

## Acceptance Criteria

- [ ] **US-069-AC1** — Given built core artifacts, when installed alone in a clean
  fixture, then direct embedding works without web, TUI or platform dependencies.
- [ ] **US-069-AC2** — Given built optional Python artifacts, when installed
  together, then every namespace has one owner and required assets/types are present.
- [ ] **US-069-AC3** — Given packed browser artifacts, when installed outside the
  monorepo, then public exports and React peers work without private deep imports.
- [ ] **US-069-AC4** — Given compatible installed packages, when Maya substitutes
  a client provider and UI component, then session behavior remains conformant.
- [ ] **US-069-AC5** — Given a pinned build cohort, when rebuilt cleanly, then
  wire/asset manifests match their advertised package compatibility.
- [ ] **US-069-AC6** — Given missing assets or incompatible protocol versions,
  when the application starts or connects, then it reports an explicit failure.
- [ ] **US-069-AC7** — Given an optional dependency change, when the complete
  release gate runs, then qualified ranges and base-dependency exclusions are checked.
- [ ] **US-069-AC8** — Given the release packet, when Maya reads the guides,
  then each platform/version claim points to executed evidence or remains unqualified.

## Edge Cases

- No source checkout is on import paths; wheels/npm tarballs are the test inputs.
- Registry publication or live deployment is not implied by local packet creation.
- Fabricated examples contain no credentials, private data or downstream identity.

## Test Scenarios

| Scenario | AC ID | Input / State | Action | Expected Result |
|---|---|---|---|---|
| Core alone | US-069-AC1 | Empty environment | Install wheel and embed | Optional dependencies absent |
| UI package | US-069-AC3 | Fresh npm project | Install packed UI/client | Types and peer resolution pass |
| Missing asset | US-069-AC6 | UI manifest references absent asset | Start carrier | Readiness refuses |
| Claims review | US-069-AC8 | Local pass, no deployed Apps evidence | Generate support table | Apps stays unqualified |

## Dependencies

US-065–068; FEAT-017; DIST-01–06; FR-70/NFR-9; ADR-022;
CONTRACT-006/007; TP-002; IP-002.

## Out of Scope

Publishing packages, live resource creation or importing an embedder's business
configuration into Tether.
