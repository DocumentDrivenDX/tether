---
ddx:
  id: US-067
  type: user-stories
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: FEAT-015
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: SD-002
      kind: informed_by
---

# US-067: Replace the Session UI While Preserving Client Behavior

**Feature**: [FEAT-015 — Composable React Interfaces](../features/FEAT-015-composable-react-interfaces.md)  
**Feature Requirements**: UI-01, UI-03, UI-04, UI-05, UI-06, UI-08  
**PRD Requirements**: FR-65, FR-68  
**Priority**: P2

## Story

**As a** Python application developer  
**I want** to compose Tether's browser session UI with my own layout and message renderer  
**So that** my users keep familiar application presentation while the session controls retain Tether's authorization and reconnect behavior.

## Context

Maya wants the browser session view to fit an existing React application without forking transport, session state, or approval behavior. She needs to inject the API client and replace visible presentation through typed slots, while the default components remain safe for arbitrary message content.

## Walkthrough

1. Maya installs only `@tether/client` and `@tether/ui` in a locked workspace; transport imports do not load React by themselves.
2. Her app creates an explicit client for `/assistant/api/v1` and provides it to the session UI.
3. The default screen lists sessions and opens one session while applying committed events in order.
4. Maya replaces the session layout and message renderer through the exported typed slots.
5. A fabricated message containing `<script>alert(1)</script>` appears as text in the default view.
6. Maya builds the reference host beneath `/assistant`; browser requests retain exact sequence and entry positions.

## Acceptance criteria

- [ ] **US-067-AC1** — Given a consumer imports only `@tether/client`, when the package is loaded, then no React module or credential singleton is loaded or created.
- [ ] **US-067-AC2** — Given an API client is passed to the React provider, when the session screen loads, then all reads and commands use that instance.
- [ ] **US-067-AC3** — Given a layout and message renderer implement the published slot types, when Maya supplies them, then the session view uses them without private imports or changed API behavior.
- [ ] **US-067-AC4** — Given a message contains `<script>alert(1)</script>`, when the default renderer displays it, then it is visible as text and no script runs.
- [ ] **US-067-AC5** — Given the event sequence is `9007199254740993`, when the browser receives and applies the event, then its resume position remains exactly `9007199254740993`.
- [ ] **US-067-AC6** — Given the reference UI is mounted at `/assistant`, when it loads its initial route and assets behind that prefix, then all UI and API URLs resolve under the configured prefix.
- [ ] **US-067-AC7** — Given a keyboard-only user opens a pending confirmation, when they approve or decline and receive an outcome, then focus and accessible status remain available without pointer input.

## Edge cases

- The API sends an unknown event type; the client validates the union and starts snapshot resynchronization.
- A stream gaps or reports lag; provisional text clears and committed history repopulates the view.
- The server reports an unsupported archive capability; the screen explains unavailable archive actions.
- A parent React application changes client identity; the provider replaces the client and discards state from the old owner.
- A custom renderer throws or returns unsafe markup; application error handling contains the component failure, and no HTML injection path is provided by the default component contract.

## Test scenarios

| Scenario | AC ID | Input / State | Action | Expected Result |
|---|---|---|---|---|
| Framework-free import | US-067-AC1 | Workspace imports `@tether/client` only | Build and inspect dependency graph | React absent; no singleton created |
| Injected instance | US-067-AC2 | Fake API records calls | Render screen and open session | All fake reads and commands use supplied instance |
| Replaceable slots | US-067-AC3 | Custom layout and renderer implement exported types | Render fabricated session | Custom components render; API behavior unchanged |
| Text safety | US-067-AC4 | Message body `<script>alert(1)</script>` | Render default message | Literal text shown; script not executed |
| Large sequence | US-067-AC5 | Event sequence `9007199254740993` | Receive then reconnect | Same exact decimal value is sent in resume request |
| Path prefix | US-067-AC6 | Mount path `/assistant` | Load app and fetch assets | Requests resolve beneath `/assistant` |
| Keyboard confirmation | US-067-AC7 | One pending fabricated approval | Navigate, submit, read result by keyboard | Focus is predictable and outcome is announced accessibly |

## Dependencies

- **Stories**: US-066 (HTTP API and remote client semantics).
- **Feature Spec**: FEAT-015.
- **Feature Requirements**: UI-01, UI-03–UI-08.
- **PRD Requirements**: FR-65, FR-68, NFR-9.
- **External**: Authenticated API from FEAT-014, React application, CONTRACT-006, selected UX interaction patterns.

## Out of scope

- Providing or storing application credentials.
- Changing Tether session authorization, approval, or history policy.
- A required component library or a customized component theme for every application.
- A promise of browser bundle size until a measured production build is retained.
