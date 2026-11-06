---
ddx:
  id: FEAT-015
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
    - id: CONTRACT-006
      kind: informed_by
    - id: US-067
      kind: informs
    - id: SD-002
      kind: informs
---

# Feature Specification: FEAT-015 — Composable React Interfaces

**Feature ID**: FEAT-015  
**Priority**: P2  
**Owner**: Product and Engineering  
**Covered PRD Subsystem(s)**: Reference Clients  
**Covered PRD Requirements**: FR-68  
**Cross-Subsystem Rationale**: None — single subsystem.

## Overview

FR-68 gives embedders a replaceable browser client and reusable interface components. The browser packages make session viewing and commands available without coupling consumers to one React layout or presentation.

## Ideal Future State

Maya installs the browser client and chooses only the layers her application needs. The transport client works without React, hooks receive an explicit client, and the session UI offers typed layout and message-rendering slots that she can replace while preserving Tether authorization and event behavior.

## Problem Statement

- **Current situation**: Tether has a reference terminal UI and no browser packages.
- **Pain points**: An embedder would otherwise build transport types, event validation, session state, safe rendering, and every control independently.
- **Desired outcome**: A reproducible set of optional packages provides a complete reference experience and supports replacement at clear public seams.

## Functional Areas

| Area | User question or job | Feature responsibility |
|---|---|---|
| Transport client | Can I call the API without adopting React? | Provide explicit base URL, credentials/fetch integration, generated types, runtime validation, and lossless counters. |
| React integration | Can my app reuse session behavior in its own component tree? | Provide hooks/provider around an injected client with one authoritative event reducer. |
| Composable UI | Can I replace layout or message presentation? | Provide typed components and safe extension slots without private imports. |
| Reference host | Can I run the example behind an application path or proxy? | Build and package assets with configurable mount and asset roots. |

## Requirements

### Functional requirements by area

#### Transport client

UI-01. `@tether/client` must expose generated HTTP types and runtime validators without importing React.

UI-02. The browser transport must preserve exact sequence, entry position, usage, and other large integer values without JavaScript `number` rounding.

#### React integration

UI-03. `@tether/react` must receive an explicit API client and must not create a process-global client or credential store.

UI-04. One reducer must apply committed changes according to their sequence and `last_in_commit` marker; provisional text must remain separate and reset on lag, reconnect, or attempt failure.

#### Composable UI and reference host

UI-05. `@tether/ui` must expose typed layout and message-rendering slots so an embedder can replace presentation without private imports.

UI-06. The default message renderer must treat content as plain text. Approval and raw payload controls must remain subject to API capabilities and current authorization.

UI-07. The reference app must mount under a configurable path and resolve built assets beneath an explicit reverse-proxy prefix.

UI-08. An embedder must be able to replace the default layout or message renderer while using the same transport client and session behavior.

### Non-functional requirements

- **Performance**: The initial browser bundle target is 250 KiB gzip, as selected in the React concern; the build must report measured size by entry point.
- **Security**: No arbitrary HTML execution, implicit cross-origin credentials, or token persistence in browser storage.
- **Accessibility**: Controls, dialogs, live updates, and errors must preserve keyboard use, focus, and accessible status announcements under the selected UX interaction patterns.
- **Compatibility**: Workspace install and builds use a committed npm lockfile and pinned compatible package graph; generated types remain in sync with CONTRACT-006.

## User stories

- [US-067 — Replace the session UI while preserving client behavior](../user-stories/US-067-replaceable-react-session-ui.md)

## Edge cases and error handling

- **A server sends an unknown event discriminator**: the runtime validator rejects the event and the client resynchronizes from a snapshot.
- **A 64-bit sequence exceeds JavaScript's exact numeric range**: it remains a decimal wire string and is parsed as `bigint`.
- **A message contains HTML or script-shaped content**: default rendering displays it as text.
- **The host uses a path prefix**: API and static assets resolve relative to configured roots without falling back to root paths.
- **The API reports an unsupported archive or detail method**: the UI disables or omits that action with an explicit reason.

## Constraints and assumptions

- Browser packages live in the npm workspace: `@tether/client`, `@tether/react`, and `@tether/ui`; React is a peer dependency of the hooks/UI layer.
- The selected stack is React 19, strict TypeScript with `noUncheckedIndexedAccess`, Vite, React Router client mode, TanStack Query, Tailwind 4 tokens, ESLint, Vitest, Testing Library, and MSW. Versions and scripts follow ADR-022.
- The API is provided by FEAT-014. Exact event and wire shapes are defined by CONTRACT-006.
- Integrator-authored renderers remain responsible for their own output safety. The default UI never trusts arbitrary HTML.

## Dependencies

- **Other features**: FEAT-013 (public client semantics and lifecycle); FEAT-014 (HTTP routes, schemas, and event feed); FEAT-017 (reproducible Python/browser package and release qualification).
- **External services**: Host application provides an authenticated same-origin API and deployment path.
- **PRD requirements**: FR-65, FR-68, NFR-9.

## Out of scope

- A mandatory React dependency for Python core or `@tether/client`.
- Owning browser authentication, application-specific layouts, or a deployment service.
- A second event ordering or session state authority in the UI.
- A required third-party component library.
