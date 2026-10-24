---
ddx:
  id: ADR-011
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-23
  authoring:
    home: repo
  links:
    - id: SPIKE-001
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-002
      kind: informed_by
    - id: CONTRACT-001
      kind: informs
    - id: CONTRACT-002
      kind: informs
---

# ADR-011: Identity Is an Explicit Credential on the Actor and Is Never Stored

## Problem

A platform serving many users calls models and downstream services as the user who asked, so each call needs that user's credential. One way to provide it is an ambient context variable that an adapter reads. That hides a dependency, is copied across thread and task boundaries by convention, breaks when work moves (a resumed turn, a background task), and is easy to leave unset in a test. A credential must also never reach a log, a cassette, a span, or a stored session.

## Decision

We will carry identity as an explicit `Credential` on the `Actor` for each call, supplied by the application when it starts or resumes a turn, never stored, and read by adapters and tools from the actor. There is no ambient identity.

- **The actor holds the credential.** `Actor` has an optional `credential` field, excluded from serialization and from its representation. A `Credential` is an object with an asynchronous `authorization()` method, so a token that expires can refresh.
- **The model adapter uses it per call.** The selected Pydantic AI model integration supplies current identity at each physical request through per-call configuration, without mutating shared client defaults. SPIKE-001/004 provide scoped mock evidence; the selected native/compatible production adapter must qualify caller/application/anonymous modes, refresh, cancellation and every SDK retry/continuation under ADR-002/003.
- **Tools read it from the context.** `ToolContext.actor` gives a tool the credential to call a downstream service as the user, through the engine's gated reads where applicable.
- **Clients bind an actor.** `Tether.client(actor)` returns a client whose commands that start model calls (`send`, `confirm`, `resume`) use that actor. A follower needs no model credential.
- **It is never persisted.** The credential is not written to the log, a summary, a cassette, a span, or an audit entry; the recorder strips the actor's identity as well as its credential.
- **Nothing runs for an absent user.** Because the credential is not stored, the engine cannot act for a user who is not present; resume is an explicit act by a present caller (ADR-010) unless the application opts in and supplies an identity.

## Why We Chose This

### Over an ambient context variable
It needs no argument passing, but it hides the dependency, fails silently when unset or lost across a boundary, and cannot express "this resume is by this person". An explicit argument is visible in every signature and every test.

### Over a credential field on the model request
It reaches the adapter but not the tools, and a request object is more likely to be dumped or logged than an actor whose field is excluded.

### Over one credential configured per adapter
It serves a single-user or service-account deployment only; the target includes multi-user platforms.

## Consequences

**Good**: Identity is visible, testable, and correct under concurrency. Resume and background work have an obvious rule. Cassettes and logs cannot contain a credential by construction.

**Trade-offs**: The application passes an actor on each call, and a long-lived client's credential can expire, so a client is built per request or its credential refreshes itself. A deployment that wants unattended resume must supply an identity on purpose.

**Drift signals to watch**: A `ContextVar` that carries identity. A credential in a log line, a span attribute, a cassette, or a stored entry. An adapter that reads a token from the environment per user. A tool that caches a credential past its call.

## Concern Impact

- **No concern impact**: The decision applies the `security-owasp` concern (no credential in logs or fixtures) and the `concurrency-model` concern (no ambient state shared across tasks).


Current identity also reaches caller-scoped storage acquisition. SessionStore.for_actor captures
a copied actor for each operation scope, preserving the credential object; no owner-keyed cached
binding is an authorization grant. Database ownership uses actor.id, while principal remains model
attribution. Followers do not need a model credential, but their database/history profiles may
require a present storage credential. Maintenance identity is explicitly selected and never a
fallback after caller failure. Provider callbacks receive separate actor copies.
