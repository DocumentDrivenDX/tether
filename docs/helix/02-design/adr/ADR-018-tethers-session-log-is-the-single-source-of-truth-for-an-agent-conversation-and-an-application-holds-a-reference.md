---
ddx:
  id: ADR-018
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
    - id: ADR-005
      kind: informed_by
    - id: ADR-017
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
    - id: architecture
      kind: informs
    - id: prd
      kind: informs
---

# ADR-018: Tether's Session Log Is the Single Source of Truth for an Agent Conversation, and an Application Holds a Reference

## Problem

An application that already has users, records, and sessions of its own embeds an agent runtime. If the application and the runtime each keep the conversation, they disagree, and a crash or a retry makes them disagree in ways that are hard to repair. The first designs said an application "subclasses the session type and stores it in its own tables", which makes two owners, and the session type carried an application-defined `flags` field with no owner. Four kinds of state are in play, and each needs one owner.

## Decision

We will make tether's session log the only record of an agent conversation, and have an application hold a reference to it, never a copy. Application state stays the application's and reaches tools through capabilities and the policy.

| State | Owner | Where it lives | Contract |
|-------|-------|----------------|----------|
| Loop state (the stream in progress, the step counter, task handles) | The engine, in memory | The process only; rebuilt from the log | CONTRACT-002 section 8 |
| Durable execution state (the lease, intents, approvals, the resume chain) | Tether and its store | The log and the lease record | CONTRACT-003 |
| Conversation history | Tether | The log; the application reads it through the client API | CONTRACT-003 |
| Application state (users, documents, entitlements, artifacts, business records) | The application | Its own storage | Reaches tools as capabilities and the policy; never in a session type |

- **The reference.** `create(ref=...)` takes an application-supplied reference, unique per owner and idempotent: creating twice with the same reference returns the same session. `find(ref)` looks one up. The application stores the tether session id or the reference, and it never copies history back.
- **Idempotent sends.** `send(request_id=...)` is idempotent: the same id with the same text returns the existing turn; the same id with different text is a conflict.
- **No application data in session types.** The `flags` field is removed. Per-turn application data travels in `Actor.attributes` or a capability.
- **Tether never reads the application's tables.**
- **Deferred.** An application-owned history port (the engine reads history from the application) is not in v1: it reintroduces two owners, and the log is what recovery, the follow feed, and the summaries are built on. Importing existing history into a new session (`seed`) is optional and not recommended before a consumer needs it.

## Why We Chose This

### Over an application-owned history port
It fits an application that must keep its own message table, and it makes the application's table and the engine's recovery two sources of truth.

### Over subclassing the session type
It puts application fields in the engine's types and makes the application's store the place the engine's state must also live.

## Consequences

**Good**: One owner per kind of state. An application can embed tether beside its own sessions and rebuild nothing after a crash. The follow feed and recovery see the whole conversation.

**Trade-offs**: An application that must keep its own copy of messages (for a compliance reason, say) reads them from the client API and keeps its copy as a derived record. The first consumer must be able to hold a reference; its adoption is the test.

**Drift signals to watch**: An application field added to a session type. A document that tells an application to subclass the session. A code path that writes history from the application into the log other than through the engine.

## Concern Impact

- **No concern impact**: This applies the `hexagonal-architecture` concern (state behind one owner per port).
