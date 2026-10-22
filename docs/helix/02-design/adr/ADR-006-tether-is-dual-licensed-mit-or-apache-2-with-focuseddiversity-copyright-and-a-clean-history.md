---
ddx:
  id: ADR-006
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: prd
      kind: informs
---

# ADR-006: Tether Is Dual Licensed MIT OR Apache-2.0, With FocusedDiversity Copyright and a Clean History

## Problem

A library meant to be embedded needs terms that adopters, including companies with legal review, accept without negotiation. It also needs a clear copyright holder and a history that carries nothing from elsewhere that should not be public.

## Decision

We will license every package of tether under either the MIT License or the Apache License, Version 2.0, at the adopter's option, with copyright held by FocusedDiversity LLC, and we will begin the repository's history at its creation, without importing history from another repository.

- Each package declares `MIT OR Apache-2.0` as its license expression and ships both license texts. The repository holds `LICENSE-MIT`, `LICENSE-APACHE`, and a `NOTICE`.
- The copyright line is `Copyright 2026 FocusedDiversity LLC`, the legal entity the operator named.
- Anything brought from another repository is read and scrubbed first, then added as a new commit. No history is imported.
- Operator decisions: Erik LaBianca, 2026-10-21 (the licenses and the clean history) and 2026-10-22 (the legal entity, FocusedDiversity LLC).

## Why We Chose This

### Over Apache-2.0 alone
It gives an express patent grant but is not compatible with some older GPL-2.0 projects. Offering MIT as well removes that obstacle.

### Over MIT alone
It is the simplest, but it carries no patent grant, which some adopters' counsel require.

### Over a copyleft license
It would restrict embedding in proprietary applications, which is the target use.

### Over importing history
It would carry authorship and content that was never reviewed for publication. A fresh history is cheap and safe.

## Consequences

**Good**: Adopters choose the terms they need. The repository can be made public without a history audit.

**Trade-offs**: Dual licensing means two texts to keep consistent. Outside contributions are governed by ADR-007. Authorship of work brought in from elsewhere is not visible in this history, so the first commit that adds such work names it by description (for example, an earlier in-house client), never by a private repository's name.

**Drift signals to watch**: A package missing a license expression or a license text. A file that carries a different license. A bulk import of another repository's history. A dependency whose license is incompatible with both.

## Concern Impact

- **No concern impact**: Selecting a license is not a concern practice.
