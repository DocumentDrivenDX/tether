---
ddx:
  id: FEAT-014
  type: feature-specification
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-005
      kind: informed_by
    - id: US-066
      kind: informs
    - id: SD-002
      kind: informs
    - id: CONTRACT-006
      kind: informs
---

# Feature Specification: FEAT-014 — HTTP API and Remote Clients

**Feature ID**: FEAT-014  
**Priority**: P2  
**Owner**: Product and Engineering  
**Covered PRD Subsystem(s)**: Client API; Reference Clients  
**Covered PRD Requirements**: FR-65, FR-67  
**Cross-Subsystem Rationale**: The HTTP adapter exposes the existing Client API to remote interface consumers while preserving the reference-client substitution and shared-session outcome in FR-65. The HTTP surface is the one capability joining these two PRD subsystems.

## Overview

FR-65 requires replaceable HTTP and browser consumers that preserve Tether client behavior. FR-67 makes that outcome usable from a remote browser through a typed HTTP protocol with the same client operations, current authorization, idempotency, and reconnect semantics.

## Ideal Future State

Maya mounts the optional API inside her authenticated application, injects a client provider, and connects a browser client. The browser sees the same authorized sessions as the terminal interface, resumes after network loss, and can resolve whether an accepted command completed without causing it to run again.

## Problem Statement

- **Current situation**: Tether exposes a concrete in-process Client. The core has no HTTP package or remote client protocol.
- **Pain points**: An embedder must translate pages, history status, approvals, stream order, and retry outcomes independently. A lost HTTP response can make command acceptance uncertain.
- **Desired outcome**: A remote consumer can implement every Client operation or receive explicit capability state, preserve authorization and data disclosure, and recover accepted outcomes from persisted facts.

## Functional Areas

| Area | User question or job | Feature responsibility |
|---|---|---|
| Client mapping | Can the browser access the same operations as a local consumer? | Map every public Client method to a versioned route or explicit unsupported capability. |
| Commands and outcomes | Can I retry safely if the network loses a response? | Preserve accepted turn IDs, idempotency, conflicts, and journal-backed result lookup. |
| Live changes | Can the browser reconnect without losing committed changes? | Preserve atomic snapshots, commit boundaries, sequence order, lag, reset, and resync behavior. |
| Authorization and content | Does a later read still use current permission? | Resolve current identity and policy on every request and stream batch; preserve history and payload disclosure states. |

## Requirements

### Functional requirements by area

#### Client mapping

HTTP-01. The HTTP API and remote Python client must map every method in the public Client protocol to a route or a capability entry that explicitly reports unsupported behavior.

HTTP-02. The API must preserve summary keyset pages, positional entry/history/effect pages, atomic snapshots, archive tiers, call detail, approvals, resolution, close, update, archive restore, and history-link operations with the semantics in CONTRACT-003/005.

#### Commands and outcomes

HTTP-03. The API must acknowledge a send after acceptance with a stable turn identity and allow a remote handle to retrieve the terminal result through a bounded owner-authorized read.

HTTP-04. A repeated send request ID with the same canonical input must resolve to the same turn. Reuse with different input must report conflict. A lost response must not be interpreted as a failed command.

HTTP-05. Disconnecting an HTTP request or browser stream must not cancel accepted execution. Cancellation must be an explicit client command.

#### Live changes

HTTP-06. The event stream must preserve owner sequence order and commit boundaries. A consumer advances its durable position only after the last event in a commit.

HTTP-07. The event stream must distinguish committed events from provisional attempt text, invalidate provisional text on reset/lag/reconnect, and require snapshot recovery after a retention gap.

HTTP-08. Stream buffers and event batches must be finite. Slow subscribers must not delay session execution or other subscribers.

#### Authorization and content

HTTP-09. The embedder must supply authentication and client construction. The API must not use a permissive identity, hidden credential, or environment-derived auth default.

HTTP-10. The API must check current owner and read policy for each request and stream batch, and require current payload-disclosure permission before raw model exchange data is returned.

HTTP-11. History readiness, denial, partiality, and outage must remain explicit. The API must not present absent or unavailable content as a successful empty transcript.

HTTP-12. The optional Python remote client must use an explicit endpoint and injected credential provider, implement the public Client protocol, and keep transport dependencies out of the base install and API package runtime.

### Non-functional requirements

- **Performance**: Preserve the configured page, byte, timeout, queue, and stream limits of the supplied Client. Numeric HTTP budgets remain those selected by the PRD benchmark baseline; no new threshold is inferred here.
- **Security**: Same-origin browser commands require CSRF protection when cookie credentials are used. Credentialed CORS requires exact configured origins. Request bodies and content are excluded from logs.
- **Reliability**: Persisted outcomes and commit sequence are the source of truth across API process replacement; in-memory request handles are not outcome authority.
- **Compatibility**: REST and event schemas are versioned and generated or checked from authoritative schemas. Potentially 64-bit counters round-trip exactly through JavaScript.

## User stories

- [US-066 — Embed an HTTP API and recover remote session outcomes](../user-stories/US-066-http-and-remote-session-parity.md)

## Edge cases and error handling

- **The API returns no response after a send was accepted**: the caller retries with the same request ID or reads the turn resource; the system does not start a second turn.
- **Permission changes during a live stream**: the API closes the stream before another protected event is disclosed.
- **The follower is behind retention or misses a commit**: the API reports resync and the client takes a new snapshot.
- **History is denied or unavailable**: the page reports the declared status or the request returns an explicit problem; it is never silently empty.
- **The installed client lacks archive support**: capabilities report unavailable and archive methods return explicit unsupported errors.

## Constraints and assumptions

- HTTP behavior is owned by the optional `novirhiza-tether-web-api` distribution and `tether_web_api` namespace; framework types do not enter `tether.client`.
- Remote Python access is an optional core extra under `tether.client`; it shares generated wire schema data without importing `tether_web_api`.
- FastAPI is selected by ADR-022 as an optional dependency. The application retains server startup, auth policy, CORS origins, deployment, and shared storage configuration.
- Exact routes, schemas, wire integer encoding, errors, and compatibility rules are in CONTRACT-006.
- Remote result recovery depends on the additive `ClientProtocol.turn_result` operation. It must be implemented and qualified before remote handle support is claimed.

## Dependencies

- **Other features**: FEAT-013 (public client construction, protocol, capabilities, and lifecycle); FEAT-017 (optional package and release qualification).
- **External services**: Host application authentication and HTTP server lifecycle.
- **PRD requirements**: FR-17–20, FR-48, FR-65, FR-67, NFR-6, NFR-9.

## Out of scope

- Building an identity provider, application-specific permission rules, or deployment service.
- React components and browser presentation, which belong to FEAT-015.
- A local browser database or a second session authority.
- Making every archive, history provider, or optional operation available in every client configuration.
