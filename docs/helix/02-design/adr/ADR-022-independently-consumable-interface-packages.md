---
ddx:
  id: ADR-022
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: architecture
      kind: informs
    - id: CONTRACT-006
      kind: informs
    - id: CONTRACT-007
      kind: informs
    - id: SD-002
      kind: informs
    - id: SD-003
      kind: informs
---

# ADR-022: Independently Consumable Interfaces in One Repository

## Problem

Embedders need typed session access without constructing private runtime objects.
They also need to replace transport, presentation and hosting independently.
Bundling optional web dependencies into core or sharing private implementation
imports would couple every application to Tether's reference interfaces.

## Decision

Maintain one Tether repository and separate installable artifacts with enforced
public dependency directions. Real embedder applications live in their own
repositories and consume released artifacts. Package boundaries, not repository
access, define supported use. FR-65 through FR-70 and NFR-9 govern this scope.

### Python distributions

| Proposed distribution | Import owner | Responsibility |
|---|---|---|
| novirhiza-tether (existing) | tether.client; internal core; tether.tui extra | Public embedding protocol/types/configuration/construction/lifecycle; authoritative behavior; optional reference terminal consumer |
| novirhiza-tether-web-api | tether_web_api | FastAPI application/router factories, HTTP schemas, SSE, explicit domain/wire translation |
| novirhiza-tether-web-ui | tether_web_ui | Built browser assets, mountable hosting and safe public runtime configuration |
| novirhiza-tether-databricks | tether_databricks | Typed platform settings, trusted identity/resource integration and carrier composition |

Artifact names are proposed and require registry/name checks before release.
Use independent top-level Python namespaces for separate distributions; do not
have several distributions write into the existing regular `tether` package.
Consumers import Tether behavior and extension ports from `tether.client` only.
Private core composition may use concrete adapters internally; public factories
must expose supported types, configuration and injected ports without requiring
private imports. Preserve current imports with a tested compatibility path.

The existing core stays at src/tether. New Python projects use
packages/web-api, packages/web-ui and packages/databricks with their own src
layouts and metadata. API/UI/carrier dependencies remain optional to core.
Remote Python client support is an optional core extra under tether.client,
with transport dependencies isolated behind explicit construction.

### Browser packages

| Proposed package | May depend on | Public responsibility |
|---|---|---|
| @tether/client | Generated wire contract and browser primitives | React-free typed commands, runtime validation, streaming/reconnect |
| @tether/react | @tether/client, React, TanStack Query | Injected client provider, hooks and one server-state cache |
| @tether/ui | Public hooks/client types and React | Composable accessible components, renderers and opt-in style tokens |
| apps/reference-web | All public browser packages | Default routing, layout and composition; no privileged runtime behavior |

packages/client-ts, packages/react and packages/ui are npm workspaces with one
package-lock.json. React is a peer dependency for reusable React packages.
Public exports block deep private imports and prevent reverse dependencies.
No module import constructs a client or starts a network request.

### Reference stack and overrides

Select FastAPI/Pydantic for the optional API; React 19, TypeScript and Vite for
the client-rendered browser application; Playwright/Chromium for browser proof.
Use strict TypeScript with noUncheckedIndexedAccess, ESLint/React hooks rules,
Vitest/Testing Library/MSW, React Router in client mode, and TanStack Query for
server state. Local UI state and provisional attempt overlays have separate
lifetimes and cannot become a durable session authority.

Use project-owned semantic accessible components initially; no third-party
widget library is required by the public UI contract. Tailwind 4 implements
reference tokens with opt-in stylesheet imports. UI replacement must not require
Tailwind. Adopt the HELIX initial JavaScript budget of 250 KiB gzip as a proposed
reference target; measure and review rather than silently widen it.

Python wire validation remains authoritative. Generate browser types and runtime
validators from the API's versioned schemas, including streaming variants absent
from ordinary OpenAPI responses. Test schema conversion and unsafe numeric ranges;
never duplicate divergent handwritten validation rules across languages.

No second Node server, server rendering or mandatory Databricks frontend library
is introduced. Static assets are built into the UI wheel; production startup does
not build JavaScript or require Node. The carrier may offer a separate source-build
recipe with a platform root package.json build hook. Do not place requirements.txt
beside its uv project: that changes platform dependency resolution.

### Platform and compatibility qualification

The Databricks carrier hosts the generic API/UI on one origin, resolving platform
environment/resources only at its composition root. Bind the platform port and
model current user identity independently of the app infrastructure principal.
Use external durable storage; never claim local JSONL restart durability on Apps.
Lakebase adoption requires its own exact-version/profile and failure evidence.

Databricks documents H2C and a 15-second SIGTERM window. Hypercorn is the candidate
ASGI runner because its documentation describes H2C support; exact proxy behavior,
SSE buffering/expiry and shutdown require a deployed compatibility spike. This is
a reversible runner selection pending that proof, not a deployed support claim.
Do not add a default Uvicorn path that silently bypasses the H2C proving gate.

Before implementation pins are accepted, a bounded compatibility task resolves
Python 3.11/current core Pydantic/Agent constraints with FastAPI, server and
optional platform SDK versions. A second check locks the browser/toolchain peer
matrix. No exact package-version support is inferred from these selections.

## Why We Chose This

### Over one repository per interface

One repository permits atomic contract changes and installed cross-package tests.
Separate repositories become appropriate for independent ownership, access or
release governance. They do not substitute for package export/import enforcement.

### Over one mandatory Python distribution

Separate artifacts let a direct embedder install core alone and let API/UI/carrier
versions evolve under an explicit tested compatibility matrix. The cost is more
build and artifact tests, which FR-70 makes part of completion.

### Over a framework-owned full-stack runtime

A static React application and mountable Python API preserve application-owned
hosting and Tether's existing Python runner. No server-rendered content or SEO
requirement justifies another production runtime. Embedders can still replace the
entire browser stack through the versioned HTTP boundary.

## Consequences

**Good**: core installation remains small; replacement uses typed public seams;
web/TUI share session authority; Databricks compatibility is isolated and measured.

**Trade-offs**: maintain Python/npm release metadata, schema generation, optional
assets and compatibility tests. A remote transport must prove parity, including
lost acknowledgments and durable outcome lookup, rather than wrap methods by name.

**Drift signals to watch**: private imports in consumers; framework types in client
protocols; leaked environment access; duplicated session reducers; deep npm imports;
committed generated build output; unqualified platform claims; independently
published packages whose declared compatibility was never installed and tested.

## Concern Impact

- Scope react-vite, frontend-architecture, ux-interaction-patterns, a11y-wcag-aa,
  e2e-playwright and databricks-apps to the affected optional interfaces/carrier.
- Override layout to npm workspaces, frontend CSS to opt-in reference tokens,
  and form validation to generated Python-wire schemas. A general remote client
  accepts an injected endpoint; the reference UI remains same-origin by default.
- Python remains governed by python-uv; reference browser tooling uses npm under
  the Python host. This does not select Bun as Tether's language runtime.

## Sources and Verification Limits

- [FastAPI independent mounting](https://fastapi.tiangolo.com/advanced/sub-applications/).
- [Vite backend integration](https://vite.dev/guide/backend-integration).
- [Databricks dependency/build behavior](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/dependencies).
- [Apps environment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env).
- [Apps hosting/shutdown requirements](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/best-practices).
- [Apps authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth).
- [Lakebase resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase).
- [Hypercorn H2C](https://hypercorn.readthedocs.io/en/latest/discussion/http2.html).

Sources establish intended integration constraints, not installed Tether support.
IP-002 schedules proofs; TP-002 records the required evidence cohort.
