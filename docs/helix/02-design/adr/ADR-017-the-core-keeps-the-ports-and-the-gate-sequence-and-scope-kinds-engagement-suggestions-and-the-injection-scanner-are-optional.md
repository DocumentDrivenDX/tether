---
ddx:
  id: ADR-017
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-003
      kind: informed_by
    - id: ADR-016
      kind: informed_by
    - id: ADR-018
      kind: informed_by
    - id: CONTRACT-002
      kind: informs
    - id: prd
      kind: informs
    - id: SD-001
      kind: informs
---

# ADR-017: The Core Keeps the Ports and the Gate Sequence, and Scope Kinds, Engagement, Suggestions, and the Injection Scanner Are Optional

## Problem

The first drafts of CONTRACT-002 carried several mechanisms that came from one consuming platform: fixed and growing scope keys with a bound-versus-held distinction, engagement and a pinned specification on the session, an `entitlement_unavailable` reason, a suggested next message, and a mandatory injection scanner whose port was never defined. They make the core larger and bake one application's semantics into every user's engine. The scanner was "mandatory", and "a security guarantee" is what readers hear in that word, while a rules-based scan is only a heuristic.

## Decision

We will keep in the core only what the gate sequence needs to be explicit and enforced, and move application semantics to optional packages or to the application.

- **Core.** The policy port and its action set (`CALL_MODEL`, `CALL_TOOL`, `RETRIEVE`, `APPROVE`, `RESUME`, `READ_SESSION`, plus namespaced actions that optional packages define); approval (ADR-015); the result-guard port and chain, which fails closed; the provider surface; held scope as an opaque map with a per-tool `requires`, decided by the policy; and the `SpecResolver` and `ScopeBinder` ports with dummies.
- **Optional `tether.scope`.** Fixed and growing keys, scope kinds, the bound-versus-held distinction (derived from the scope recorded on each model-call entry, so it needs no stored state), `drawn_from` facts, the bound-context floor guard, and widening as a namespaced action. A multi-user platform that needs those semantics installs it.
- **Optional `tether.guards`.** The rules-based injection heuristic as a guard, and `default_guards()`.
- **The application's, not the core's.** Engagement and routing, and the specification pinned for a session (the `turn_started` entry records the specification's id, version, and digest, and the resolver may pin it), and suggestions for a next message. These leave the core types: `Action.ENGAGE`, `SessionState.engaged`, `pinned`, and `flags`, the `entitlement_unavailable` reason, `after_answer`, `TurnResult.suggestion`, and the `suggest` option.
- **Scanning is a heuristic.** No guard is mandatory. The engine runs the guards it is given, in order, and fails closed when one raises. `default_guards()` is shipped, opt-in, and enabled in the demo. Documents describe the injection guard as "a signal, not a security boundary": policy, scope, and approval are the controls.
- **The test for membership.** An item is in the core only if it can be done only inside the engine's pipeline, and the platform's authorization boundary or two of the three non-coding scenarios (research with citations, document extraction, an approved external action) need it.

## Why We Chose This

### Over keeping everything in the core
It makes the first consumer's semantics the library's, against the `scope-discipline` concern, and every embedder pays for what they do not use.

### Over removing scope entirely
A multi-user platform that restricts a session to the values it was first sent under needs it; it belongs in a package, not nowhere.

### Over a mandatory scanner
It invites the reading that the library secures a deployment against prompt injection. A heuristic that a paraphrase defeats cannot carry that claim.

## Consequences

**Good**: A smaller core with an explicit gate sequence. The undefined scanner port stops being an open item, because the result-guard port is the port. The document set no longer overstates security.

**Trade-offs**: A platform that wants the first consumer's scope semantics installs a package and configures it. The optional packages need their own conformance suites. Removing fields from core types means the first consumer's adapter maps them.

**Drift signals to watch**: A core type that names scope, engagement, or suggestions. A guard the engine requires. A document that calls the injection guard a protection. An optional package imported by `tether.engine`.

## Concern Impact

- **No concern impact**: This applies the `scope-discipline` and `modularity-and-encapsulation` concerns.
