---
ddx:
  id: US-068
  authoring:
    home: repo
  links:
    - id: FEAT-016
      kind: informed_by
    - id: CONTRACT-007
      kind: informed_by
    - id: SD-003
      kind: informed_by
    - id: TP-002
      kind: informs
---

# US-068: Run Shared Tether Sessions in a Databricks Apps Carrier

**Feature**: FEAT-016 — Databricks Apps Carrier  
**Feature Requirements**: DBC-01 to DBC-11  
**PRD Requirements**: FR-69  
**Priority**: P2

## Story

**As a** platform engineer, Sam  
**I want** to run Tether's browser client in a Databricks Apps workspace using the reusable carrier example  
**So that** authorized users can continue their shared sessions across browser and terminal clients without mixing user authority with app infrastructure identity.

## Context

Databricks Apps supplies app identity and can forward current user identity, while Tether keeps session ownership and policy at its client boundary. A shared database role or an expired user token must not silently grant a different authority. The carrier must use an explicitly qualified durable profile and make recovery limits visible.

## Walkthrough

1. Sam installs the optional carrier distributions and reviews the fabricated Databricks Apps example.
2. Sam supplies the workspace's approved app authorization, user scopes, database endpoint and separately administered migration/role setup.
3. Sam validates and deploys the bundle; the example starts its one ASGI host and serves the API and browser assets.
4. A fabricated user opens the browser and creates a session; the TUI connects to the same host and owner namespace.
5. The user follows the session, completes a pending approval, disconnects, reconnects and sees the committed result.
6. Sam inspects metadata-only logs and profile evidence for the selected platform and storage configuration.

## Acceptance Criteria

- [ ] **US-068-AC1** — Given a deployed example with its declared resources and a ready selected store, when a user creates a session in the browser, then an authorized TUI client for the same owner namespace can read that session through the public client boundary.
- [ ] **US-068-AC2** — Given two authenticated users with distinct fabricated identities, when each lists and reads sessions, then each sees only sessions authorized for their own identity.
- [ ] **US-068-AC3** — Given a protected request with missing, expired, revoked or untrusted user authorization, when the request reaches the carrier, then it is refused without substituting the app service principal as the Tether owner or user credential.
- [ ] **US-068-AC4** — Given an approved current user and a separate app principal, when the carrier performs a user-authorized operation and a shared infrastructure operation, then each uses its selected authority and neither identity widens the other's Tether permissions.
- [ ] **US-068-AC5** — Given a session with a pending approval, when a user answers from the browser while another client submits a competing answer, then Tether's one-winner behavior determines the committed outcome and reconnect shows that outcome.
- [ ] **US-068-AC6** — Given an OAuth database credential that expires before a new physical connection, when the selected profile opens that connection, then it obtains a current credential or refuses without logging or persisting the expired or refreshed token.
- [ ] **US-068-AC7** — Given the example lacks an explicit required schema migration or database role, when it starts, then readiness fails and the carrier does not implicitly migrate or widen database authority.
- [ ] **US-068-AC8** — Given an accepted session operation when Databricks sends SIGTERM, when the carrier reaches its configured deadline within the platform's 15-second allowance, then it returns a bounded shutdown report naming outstanding work and retains ownership until physical exit; if the platform force-kills the process first, restart reports committed facts and does not infer the outcome of an unsettled external effect.
- [ ] **US-068-AC9** — Given the example is built from its committed lock files, when an embedder validates and deploys it, then the bundle contains a complete one-host app composition and no customer data, credential, or required Node runtime.
- [ ] **US-068-AC10** — Given fabricated message and credential markers in a session, when the carrier emits default logs, then neither marker appears in application or platform-captured logs.

## Edge Cases

- **Local development sends forged forwarded headers**: the explicitly selected local auth provider ignores platform identity headers.
- **Apps proxy changes an SSE connection or drops events**: the browser reboots from an atomic snapshot and exposes any unavailable history or retention gap.
- **Lakebase is reachable but its Tether schema is old or unmanaged**: startup refuses readiness pending explicit migration and verification.
- **Shutdown exceeds the platform deadline**: no completion claim is made for outstanding work; durable state and the unknown-effect barrier govern recovery.

## Test Scenarios

| Scenario | AC ID | Input / State | Action | Expected Result |
|---|---|---|---|---|
| Browser session appears in TUI | US-068-AC1 | Fabricated user `user-a`, shared test app and one qualified test namespace | Create a browser session, then read through the injected TUI client | Same session id and committed summary are visible to `user-a` |
| Owner isolation | US-068-AC2 | `user-a` owns one fabricated session; `user-b` owns another | List and read as each actor | Neither actor receives the other's session or content |
| Identity refusal | US-068-AC3 | Request has no current user token and app identity is available | Request a protected session read | Refusal; no app-principal fallback and no storage read |
| Approval race | US-068-AC5 | One pending invocation and two authorized clients | Submit competing answers and reconnect both clients | One decision commits; feed/snapshot reports the committed state |
| Token rotation | US-068-AC6 | Test credential expires before a fresh connection is opened | Acquire a connection through the selected profile | A fresh token is obtained, or the operation refuses without token exposure |
| Missing role | US-068-AC7 | Test schema exists without the required app role/migration | Start the carrier | Readiness fails; schema and roles remain unchanged |
| Bounded shutdown report | US-068-AC8 | Durable intent committed and effect outcome unknown | Stop admission; reach configured deadline before the external effect settles; reopen | Report names outstanding work, ownership remains retained until exit or kill, and the session remains blocked for authorized resolution with no inferred success |
| Locked deployment bundle | US-068-AC9 | Fabricated app settings and committed Python/browser locks | Validate and build the example | App resources resolve and static UI is included; no runtime Node requirement |
| Log privacy | US-068-AC10 | Fabricated marker strings in user token and session/tool content | Exercise success and error paths, then inspect captured logs | No credential or content marker appears |

## Dependencies

- **Stories**: US-065 public client and TUI parity; US-066 HTTP API; US-067 browser interface; US-069 package and release compatibility.
- **Feature Spec**: FEAT-016.
- **Feature Requirements**: DBC-01 to DBC-11.
- **PRD Requirements**: FR-65 to FR-70, with FR-69 as this story's direct requirement.
- **External**: CONTRACT-003 client behavior; CONTRACT-006 HTTP surface; CONTRACT-007 Databricks carrier; SD-003 architecture; Databricks Apps and the selected PostgreSQL-compatible deployment profile.

## Out of Scope

- A customer-specific app, workspace, database schema, tool, model, deployment or dataset.
- A guarantee that Lakebase has the same behavior as the qualified PostgreSQL 18 profile.
- Production deployment, migration, or resource creation in an embedder's workspace.
- Completion of external effects when the platform force-kills a process.
