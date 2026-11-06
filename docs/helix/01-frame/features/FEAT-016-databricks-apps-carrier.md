---
ddx:
  id: FEAT-016
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-011
      kind: informed_by
    - id: ADR-013
      kind: informed_by
    - id: ADR-020
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: CONTRACT-003
      kind: informed_by
    - id: CONTRACT-007
      kind: informs
    - id: SD-003
      kind: informs
    - id: TP-002
      kind: informs
---

# Feature Specification: FEAT-016 — Databricks Apps Carrier

**Feature ID**: FEAT-016  
**Priority**: P2  
**Owner**: Tether maintainers  
**Covered PRD Subsystem(s)**: Databricks Apps carrier  
**Covered PRD Requirements**: FR-69  
**Cross-Subsystem Rationale**: The carrier is one deployable composition capability. It connects the existing client and web capabilities under one app lifecycle; it does not own either capability's behavior.

## Overview

FEAT-016 provides an optional, reusable carrier for running Tether's public client and web interfaces in Databricks Apps. It gives an embedder a complete example that separates the current user from the app's shared infrastructure identity and preserves Tether session behavior through app restarts.

## Ideal Future State

A platform engineer can install the optional carrier, configure an app with the platform's approved identity and resource grants, and operate one Tether app with the browser interface and existing TUI using the same authorized session service. User access remains current at every request; app identity handles only explicitly shared infrastructure work. The example contains no customer data or private configuration and is reproducible from its declared dependencies.

## Problem Statement

- **Current situation**: Tether has a qualified Python client and Postgres implementation. Databricks Apps user authorization, resource setup, token lifetime, HTTP/2 cleartext ingress, static asset hosting, shutdown and Lakebase behavior have no Tether carrier qualification.
- **Pain points**: An embedder must currently assemble those concerns and could mistake app-level authority for the current user's authority or treat local app files as durable session storage.
- **Desired outcome**: One complete example composes the public interfaces, identifies the user and app roles separately, declares its external resources and reports failures without leaking credentials or claiming unqualified durability.

## Functional Areas

| Area | User question or job | Feature responsibility |
|---|---|---|
| Identity and authority | Which identity is making this operation, and what can it access? | Keep current user identity, app identity, and Tether storage ownership distinct; refuse missing required identity. |
| App resources and lifecycle | What resources does the app need, and what happens during shutdown or restart? | Declare explicit resource, migration, readiness and cleanup ownership with recoverable state. |
| Reusable carrier example | Can I reproduce the supported composition without private setup? | Provide a complete, documented, customer-data-free app example using public factories. |

## Requirements

### Functional Requirements by Area

#### Identity and authority

- **DBC-01** — The carrier must establish a current caller identity for every protected browser or API request and stream.
- **DBC-02** — The carrier must distinguish the current user identity from the app service principal and from Tether's logical session owner.
- **DBC-03** — When required user identity or a needed credential is missing, expired, revoked, or unverifiable, the carrier must refuse the operation without substituting app authority.
- **DBC-04** — The carrier must never persist or log credentials, access tokens, message content, or tool content.

#### App resources and lifecycle

- **DBC-05** — The carrier must declare required platform resources, authorization scopes, database roles, schema migrations and external settings explicitly.
- **DBC-06** — The carrier must use a storage profile whose Tether capabilities are independently qualified. App-local files cannot be presented as durable session/history storage.
- **DBC-07** — The carrier must preserve Tether's current authorization, approval, recovery, history-readiness and unknown-effect behavior across disconnects and restarts.
- **DBC-08** — The carrier must close owned resources within the platform's 15-second termination allowance when possible and leave accepted or uncertain work recoverable if forced termination interrupts cleanup.

#### Reusable carrier example

- **DBC-09** — The example must compose the public Tether client, HTTP API and browser UI boundaries in one app host.
- **DBC-10** — The example must run without customer data or credentials in the repository and must state every required external resource and owner-supplied value.
- **DBC-11** — The example must be reproducible from locked dependencies and distinguish source facts, demonstrated behavior and platform profiles that still require live qualification.

### Non-Functional Requirements

- **Security**: No credential or token may appear in files, logs, spans, cassettes or stored Tether facts. Required identity failures refuse before protected work.
- **Reliability**: Shutdown observes Databricks Apps' 15-second SIGTERM window; the carrier makes no completion promise after force termination. Durable recovery claims apply only to a separately qualified database profile.
- **Compatibility**: The example must declare and lock every Python and browser build dependency and pass the selected install, type, boundary and real-platform suites before support is claimed.

## User Stories

- [US-068 — Run shared Tether sessions in a Databricks Apps carrier](../user-stories/US-068-databricks-shared-session-carrier.md)

## Edge Cases and Error Handling

- **Missing or expired user authorization**: refuse before reading or changing a protected session; app identity cannot replace the user.
- **Forwarded identity outside the configured platform ingress**: reject it as untrusted; local development requires an explicitly chosen local auth provider.
- **Lakebase token expires between pooled connections**: request a fresh credential for new connections or fail visibly; do not reuse an expired token or persist it.
- **Database migration or role setup is absent**: readiness fails with a content-free diagnostic; ordinary startup does not mutate an unmanaged schema.
- **Local filesystem disappears after app restart**: session durability comes from the selected external store and history provider; missing original history remains explicit.
- **SIGKILL interrupts shutdown**: recover from committed Tether facts and report unknown external effects under CONTRACT-003; never infer completion from process exit.
- **SSE or browser reconnect follows a gap**: rebootstrap through the client snapshot and expose reset/readiness outcomes.

## Constraints and Assumptions

- Databricks Apps is an optional deployment target, not a base dependency or required Tether runtime.
- The embedder supplies its app resource grants, user authorization scopes, database endpoint, migration authority, storage roles, model/tool configuration and business data.
- The Databricks Apps shared principal may own infrastructure resources; it does not become the owner of each user's session or model call.
- Current Databricks documentation describes platform behavior but does not qualify Tether's selected server, Lakebase transaction profile, role isolation or OAuth refresh behavior.

## Dependencies

- **Other features**: FEAT-009 Reference Clients; FEAT-014 HTTP API; FEAT-015 Browser UI; FEAT-017 Packaging and Compatibility.
- **External services**: Databricks Apps ingress and authorization, selected Databricks SDK capabilities, and a separately qualified Postgres-compatible service such as Lakebase.
- **PRD requirements**: FR-65 through FR-70 and NFR-9.
- **Contracts/design**: CONTRACT-003, CONTRACT-006, CONTRACT-007, SD-003.

## Out of Scope

- A second agent runtime, AgentKit wrapper, session supervisor, or business workflow.
- Generic customer authentication, data schema, resource provisioning policy, or role/RLS policy.
- Customer-specific app deployment, workspace settings, secrets, tools, models, or datasets.
- Claiming Lakebase or any Databricks service is covered by the existing PostgreSQL 18 qualification without its own evidence.
- Using Unity Catalog volumes or app-local files as an implicit replacement for Tether's selected live store or authoritative history provider.
