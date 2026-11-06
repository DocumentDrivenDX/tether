---
ddx:
  id: TD-067
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-067
      kind: informed_by
    - id: FEAT-015
      kind: informed_by
    - id: FEAT-014
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: ADR-022
      kind: informed_by
    - id: STP-001
      kind: informs
    - id: TP-002
      kind: informs
---

# Technical Design: TD-067 — Replaceable React Session UI

**User Story**: [[US-067]] | **Feature**: [[FEAT-015]] | **Solution Design**: [[SD-002]]

## Scope

Build the optional TypeScript transport, React hooks, composable UI, and reference app for [US-067](../../01-frame/user-stories/US-067-replaceable-react-session-ui.md). Reuse the API wire and event contract in CONTRACT-006; do not define new Python or HTTP interfaces here.

## Technical Approach

**Strategy**: Add a lockfile-backed npm workspace with three independently importable packages and a client-rendered reference application. `@tether/client` owns fetch, runtime validation, exact wire values, and reconnect. `@tether/react` receives an explicit client. `@tether/ui` composes accessible views from typed slots. The reference app demonstrates the full path behind a configurable prefix.

**Key Decisions**:

- Use the ADR-022 React 19, Vite, strict TypeScript, `noUncheckedIndexedAccess`, React Router client mode, TanStack Query, Tailwind 4 opt-in tokens, Vitest/Testing Library/MSW, and pinned Playwright/Chromium stack.
- Generate TypeScript wire types and validators from the versioned OpenAPI/event schemas. Parse potentially 64-bit decimal strings as `bigint` at the transport edge; do not convert to `number`.
- TanStack Query is the one server-state cache. A single reducer applies ordered feed events into its cache; provisional attempt text lives in component-scoped ephemeral state and clears on reset, lag, failure, identity change, or resnapshot.
- Keep layout and message-renderer slots typed and narrow. The default renderer treats messages as text and exposes no raw HTML escape hatch.
- Use relative API and asset URLs resolved from the supplied application base path so reverse-proxy mounts stay intact.

**Trade-offs**: The package split makes transport, hooks, and UI useful independently but adds generated-code and compatibility gates. A single cache reducer reduces divergent event ordering, while package consumers remain responsible for replacing their own visual layout safely.

## Component Changes

### New: `@tether/client`

- **Purpose**: Framework-free browser client for CONTRACT-006 REST and SSE.
- **Interfaces**: Input: explicit base URL, fetch integration, credential mode / Output: validated typed pages, command results, and normalized feed events.
- **Files**: `packages/client-ts/src/client.ts`, `transport.ts`, `wire/generated.ts`, `wire/validators.ts`, `events.ts`, and package entrypoints.

### New: `@tether/react`

- **Purpose**: Injected-client provider/hooks, shared session query keys, and the feed-to-cache reducer.
- **Interfaces**: Input: explicit `@tether/client` instance / Output: typed hooks and state selectors.
- **Files**: `packages/react/src/provider.tsx`, `queries.ts`, `feed-reducer.ts`, `use-session.ts`, and `use-feed.ts`.

### New: `@tether/ui` and `apps/reference-web`

- **Purpose**: Accessible session controls and a real deployment-path example with replaceable layout/message presentation.
- **Interfaces**: Input: hooks, typed slots, and mount configuration / Output: composable UI and built assets.
- **Files**: `packages/ui/src/components/`, `slots.ts`, `styles/tokens.css`, `apps/reference-web/src/`, `vite.config.ts`, and `src/tether_web_ui/assets/` packaging output.

### New: workspace verification

- **Purpose**: Reproducible builds, schema drift checks, package boundary checks, unit/component tests, and browser evidence.
- **Interfaces**: Input: committed npm lockfile and schemas / Output: deterministic package artifacts and retained test reports.
- **Files**: root `package.json`, `package-lock.json`, `tsconfig.base.json`, package scripts, and `tests/ui/`.
- **Boundary**: Register `npm run check:boundaries` with the first workspace task and add negative controls for React imports in `@tether/client`, ambient client creation in `@tether/react`, and Python/API imports from browser packages.

## API/Interface Design

| Surface | Governing Contract | Story-Level Usage |
|---------|--------------------|-------------------|
| REST and event schemas | CONTRACT-006 | Generated transport types and runtime validators; do not hand-edit generated output |
| Client construction and feed methods | CONTRACT-006 | Reference app injects base URL/fetch/auth mode; React receives the instance |
| Slots and safe message rendering | CONTRACT-006 | Replace layout or renderer through exported types while commands still call the supplied client |

## Data Model Changes

No durable database model is added. TanStack Query holds the one in-memory server-state cache for the current identity; provisional reply text is transient and never becomes committed history. Identity changes clear query/feed state before the new client is used.

## Integration Points

| From | To | Method | Data |
|------|----|--------|------|
| `@tether/ui` | `@tether/react` | React imports | Hooks, provider, state selectors |
| `@tether/react` | `@tether/client` | Injected instance | Typed requests, commands, stream and errors |
| `@tether/client` | `tether_web_api` | REST and SSE | Versioned JSON schemas and commit-aware resume IDs |
| Reference app | `tether_web_ui` assets | Build/package mount | Base-path-aware static assets |

### External Dependencies

- **Authenticated API host**: Supplies the same-origin API and current application identity. On expiry, the client stops protected reads and waits for host reauthentication; it never falls back to stored credentials.
- **OpenAPI/event schemas**: Generation input. A schema mismatch blocks release and is resolved by updating CONTRACT-006 and regenerating.

## Security

- **Authentication**: Use host-managed same-origin credentials configured on the injected client; store no token in local/session storage.
- **Authorization**: Render optional actions from capabilities and server responses; a hidden button is not treated as authorization.
- **Data Protection**: Default message renderer uses React text nodes. Raw call details remain behind API authorization. Error reporting excludes message and credential values.
- **Threats**: Script-shaped messages, unknown event variants, cross-owner cache reuse, CSRF assumptions, and malicious extension renderers. Validate all wire events, clear cache on identity change, require the API's CSRF policy, and document that custom renderers own their output safety.

## Performance

- **Expected Load**: Interactive single-user session views; concrete session and stream volumes remain governed by API bounds.
- **Response Target**: Record per-entry production build size against the proposed 250 KiB gzip initial JavaScript target; treat the budget as provisional until the first measured baseline is reviewed.
- **Optimizations**: Split reference routes, keep `@tether/client` framework-free, avoid duplicate event state, and render bounded pages only.

## Testing

- [ ] **Unit**: `packages/client-ts/test/validators.test.ts`, `events.test.ts`, and `packages/react/test/feed-reducer.test.ts`; malformed unions, bigints, commit boundaries, lag/reset, identity clearing.
- [ ] **Integration**: `packages/ui/test/session-screen.test.tsx` with Testing Library and MSW; injected client, custom slots, denied capability, and safe text rendering.
- [ ] **Contract**: `scripts/check-wire-generation.ts` and API schema parity test; clean regeneration must produce no diff and every discriminator must validate.
- [ ] **Security**: UI render test for script-shaped content, no credential persistence/import singleton, and identity-switch cache clear.
- [ ] **Browser**: `tests/ui/e2e/session-flow.spec.ts` with pinned Playwright/Chromium, production assets, real API fixture, keyboard approval, path-prefix assets, reconnect after lag, and exact large sequence resume.
- [ ] **Acceptance mapping**: `US-067-AC1` framework-free import; `AC2` injected instance; `AC3` replaceable slots; `AC4` text-safe default; `AC5` exact sequence; `AC6` path prefix; `AC7` keyboard and accessible outcome.

## Migration & Rollback

- **Backward Compatibility**: Python core and existing TUI remain usable without npm packages. Browser packages are independently versioned and declare supported HTTP major ranges.
- **Data Migration**: None; browser query cache is transient.
- **Feature Toggle**: Embedders opt in by installing the desired npm packages and mounting the reference/static assets.
- **Rollback**: Pin the previous npm package set or unmount the reference UI; API and persisted sessions continue independently. Do not publish mixed generated schemas and clients.

## Implementation Sequence

1. Create the locked workspace, schema-generation check, and framework-free `@tether/client`; register and run `npm run check:boundaries`, then test import graph and exact wire integers.
2. Add `@tether/react` provider and reducer that writes into TanStack Query; test commit/reconnect transitions.
3. Add `@tether/ui` slots and safe accessible defaults, then the reference app and packaged path-aware assets.
4. Run Vitest/Testing Library and production Playwright against the packaged build; retain dependency graph, schema drift, accessibility, and measured bundle reports.

**Prerequisites**: FEAT-014 API and versioned schemas; CONTRACT-006; ADR-022; HTTP fixture and result recovery behavior from TD-066.

## Risks

| Risk | Prob | Impact | Mitigation |
|------|-----|--------|------------|
| Generated types drift from server behavior | M | H | CI regenerates and compares; compatibility test uses OpenAPI and event schemas |
| UI state diverges after feed lag or identity change | M | H | One reducer updates the single query cache; resnapshot and cache-clear browser journeys |
| Proxy path breaks asset discovery | M | M | Production-build browser test at a non-root prefix |
| Initial bundle exceeds target | M | M | Measure baseline by entry point and split optional routes before making target binding |
