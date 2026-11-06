---
ddx:
  id: concerns
  type: concerns
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: prd
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-003
      kind: informed_by
    - id: ADR-012
      kind: informed_by
---

# Project Concerns

Project Concerns declare active cross-cutting context for downstream work. They are not principles, requirements, ADRs, test plans, or implementation tasks.

tether is an embedded library with reference clients and optional HTTP API/browser UI components (FR-65). Interface components use typed dependency injection and depend only on the public client boundary (NFR-9). ADR-022 selects FastAPI, React/Vite/TypeScript and Playwright for the optional reference stack. Databricks Apps is the carrier deployment target. Core has no auth-provider or deployment requirement; typed identity integration is supplied by its embedder. Deployment and session storage remain application-owned.

The Python runtime selection is retained after the Python/TypeScript and AgentKit evaluation (ADR-003). Python embedding and browser usability are equally important; the consuming application owns its existing server and browser contract. Databricks Apps is an application deployment target, not a required dependency of this library. ADR-003 selects Pydantic AI Agent as the single internal runner. No AgentKit wrapper or second harness runtime is selected. Optional reference web components must also work within an application-owned server; they do not require a separate service.

## Active Concerns

| Concern | Source | Areas | Why Active | Key Practices |
|---------|--------|-------|------------|---------------|
| `python-uv` | library; slot `language-runtime`; operator-override (`concerns.local.yml`) | `area:core`, `area:sessions`, `area:client-api`, `area:telemetry`, `area:testkit`, `area:cli`, `area:tui`, `area:api`, `area:carrier` | The library and Python interfaces are Python. | `uv` for environments, locks, and builds; `pyproject.toml` for all metadata; `ruff check` and `ruff format --check` and `pyright` gate every change; `pytest` with branch coverage and `hypothesis`; the package is typed (`py.typed`); a library's `requires-python` is the lowest runtime a consumer runs (3.11, an assumption recorded in the PRD) and CI tests on it |
| `hexagonal-architecture` | library; slot `architecture-style`; operator-override (`concerns.local.yml`); ADR-003 | all | The salient fact is one core driven by many interfaces (reference clients, a consumer's server, tests) and driving many adapters (models, stores, telemetry, policy). Ports are the product. | The core imports only its own types and ports; every external system is an adapter behind a port the core owns; the application is the composition root; every port has a dummy and a conformance suite run against every implementation (including the dummy) |
| `modularity-and-encapsulation` | library | all | Boundaries are what the project sells; a boundary no check enforces erodes. | Architecture records module ownership, public surfaces, allowed and forbidden imports, integration owners, construction policy, and a boundary command; the command runs locally, in the pre-commit hook, and in CI; boundary negative controls exist |
| `concurrency-model` | library | `area:core`, `area:sessions`, `area:client-api` | The library is asynchronous throughout (ADR-001) and serves many sessions on one event loop. | One event loop; plain functions offloaded to a bounded thread pool with context copied; one turn per session; a slow consumer never blocks the loop (bounded queues with coalescing); cancellation is structured and leaves the session consistent; no blocking call inside `async def` |
| `testing` | library | all | The value of the library is that the loop is testable alone. | Stubs and dummies over mocks; replay of recorded fabricated conversations; property tests for the gate and cursor invariants; conformance suites per port; benchmarks with ratchets for the client API; a test is never skipped or deleted to hide a regression; no `time.sleep` in a test |
| `o11y-otel` | library | `area:telemetry`, `area:api`, `area:carrier` | Telemetry is a feature: spans and audit entries for every turn, model call, and tool call. | Applicable sections: agent run diagnostics and GenAI operations (the model-call spans); the tracer is a port with a no-op default and an OpenTelemetry (OTel) adapter as an extra; context propagates across awaits; spans and audit entries carry identifiers, sizes, and reasons and never message or tool content; core-only embedding has no service obligations; optional API/carrier uses bounded request/stream/shutdown metadata |
| `resilience` | library | `area:core`, `area:sessions` | Models and stores fail; the loop must fail in defined ways. | Gates fail closed; one layer retries and owns the policy; every external call has a bound; a failed step is recorded and a turn ends with a typed stop reason; crash recovery is idempotent |
| `security-owasp` | library | all | The library handles identity per call and runs tool code. | No credential or private datum in the repository, fixtures, cassettes, or logs; the recorder strips identity and accepts only fabricated prompts; library errors never echo response bodies; dependencies pinned and audited; inputs from models validated before use |
| `model-provider-independence` | project-local; ADR-012 | all | Local and sovereign models remain first-class; optional SDK use alone does not establish hosted-provider lock-in. | Provider SDKs stay out of the base install and public core types; explicitly selected optional adapters may import them directly or transitively, with disclosed and qualified dependency closure. Model backends, identity and configuration remain replaceable and explicit. Provider-only features are optional capabilities. The demo and deterministic tests need no network or hosted account. ADR-002/003 select Pydantic AI Agent/model reuse. The base includes the provider-neutral slim runtime; selected optional provider SDK closures still require qualification. |
| `scope-discipline` | library | all | The non-goals are the main defense against turning into a framework. | Each feature traces to the vision; an adjacency named in the non-goals needs an operator decision before it is built |
| `code-shape-ceilings` | library | all | Small modules are what coding agents and reviewers read and edit reliably. | Global size and complexity ceilings checked in CI with no per-file exceptions; the ceiling is lowered as files are split, never raised to pass a change |
| `react-vite` | library; operator selection for reference UI, ADR-022 | `area:ui` | Optional browser UI is personalized, interactive and client-rendered; no SEO/SSR requirement. | React 19, Vite, strict TypeScript/noUncheckedIndexedAccess; npm workspaces with package-lock; React Router client mode; TanStack Query sole server-state cache; opt-in Tailwind 4 tokens; Vitest/Testing Library/MSW; 250 KiB gzip initial JS budget pending baseline |
| `frontend-architecture` | library; ADR-022 | `area:ui` | Independent clients/components must not duplicate server authority. | Remote state has one query-cache owner; ephemeral attempt overlays remain transient; local drafts/selection stay local; loading/empty/error/success per route |
| `ux-interaction-patterns` | library; ADR-022 | `area:ui`, `area:tui` | Session approvals/recovery must remain usable through substituted views. | Keyboard/focus, current-location cues, preserved input, pending/error recovery and high-impact decisions have stable story ACs and observed surface tests |
| `a11y-wcag-aa` | library; ADR-022 | `area:ui` | Reference UI must support keyboard access and readable status feedback. | WCAG 2.2 AA target; semantic elements, labeled controls, focus order, zoom/contrast and screen-reader checks; automated axe supplements manual evidence |
| `e2e-playwright` | library; slot override in concerns.local.yml | `area:ui`, `area:testkit` | Browser journeys need real production assets/API evidence. | Pinned Playwright/Chromium, deterministic fabricated fixtures, per-route/workflow assertions, aria-current navigation, reviewed screenshots and videos; zero skips |
| `databricks-apps` | library; deploy-target override scoped to reference carrier, ADR-022 | `area:carrier`, `area:api`, `area:ui` | Optional carrier must satisfy actual managed hosting constraints. | Locked uv project without requirements.txt; existing resources and least privilege; external durable state; per-request user identity; deployed OBO negative controls; proxy/streaming/H2C/restart qualification |

## Project Overrides

| Concern | Practice | Override | Authority |
|---------|----------|----------|-----------|
| `python-uv` | Configuration: one central `pydantic-settings` `BaseSettings` class | The core defines plain `pydantic` configuration models and reads no environment variable, so the core does not depend on `pydantic-settings`. `pydantic-settings` is used only by the reference clients, as their composition root. | ADR-003 |
| `model-provider-independence` | (project-local; no library practice) | SDK imports are confined to selected optional adapter boundaries; base packaging and public core types remain provider-SDK-free. Reuse needs a documented integration rationale, not proof that implementing the protocol directly is impossible. | ADR-012 |
| `hexagonal-architecture` | Core depends only on its own types/ports at external boundaries | The selected internal tether.runtime.pydantic_ai integration imports pydantic-ai-slim Agent/Conversation/capability APIs. Engine uses Tether-owned callbacks/types; external I/O remains behind ports and public core payloads contain no SDK types. Runtime reuse does not require a second public runner port. | ADR-003 |
| `python-uv` | Python 3.12 or later when the runtime allows | Python 3.11 is the selected release floor. Qualify later versions separately; a downstream consumer needing another floor triggers a compatibility decision. | PRD technical context and recorded Python 3.11 qualification |
| `react-vite` | Single frontend directory; API-relative URLs and styling defaults | Use npm workspace packages/client-ts, react, ui and apps/reference-web. Reusable transport receives an injected base URL; reference app uses relative API URLs. Tailwind styles are opt-in and scoped to UI. Generated Python wire schema supplies browser validation rules; Python revalidates authoritatively. | ADR-022 |
| `databricks-apps` | Reference ASGI host, automatic build and platform identity | Hypercorn is a candidate pending actual H2C/proxy proof. Prebuilt UI wheel assets need no Node at runtime. Source-build carrier mode supplies root package.json build if selected. Carrier owns SDK/env; core never reads environment. No live token is fabricated by local run. | ADR-022 |

## Area Labels

This project uses the following area labels for concern scoping:

- `area:core` — types, ports, the Agent integration, tool mediation and gates
- `area:sessions` — the entry log, stores, the turn lease, recovery
- `area:client-api` — the UI boundary: list, read, follow, commands
- `area:telemetry` — spans, audit entries, usage accounting
- `area:testkit` — dummies, conformance suites, cassettes, benchmarks
- `area:cli` — the command-line reference client
- `area:tui` — the terminal UI reference client
- `area:docs` — HELIX artifacts and user documentation
- `area:api` — optional HTTP transport and remote Python client
- `area:ui` — TypeScript browser client, hooks and React presentation
- `area:carrier` — platform composition, packaging and Databricks deployment

## Concern Conflicts

| Conflict | Resolution |
|----------|------------|
| `python-uv` configuration practice vs the core reading no environment | The core takes configuration objects; the clients' composition root uses `pydantic-settings` (override above) |
| `concurrency-model` (async only) vs tool authors who write plain functions | The engine offloads plain functions to worker threads; `async def` remains first-class (ADR-001) |
| `testing` (replay) vs `security-owasp` (no private data) | Recording accepts only fabricated prompts and writes only what the port carries (CONTRACT-001) |

## Observability Applicability

`o11y-otel` applies for agent run diagnostics (turn, model-call, and tool-call spans and the audit record) and GenAI operations (model-call attributes such as tier, token usage, and request id). Service signals apply only to optional API/carrier hosting: bounded request/stream health and shutdown metadata. They remain excluded from core-only embedding obligations. A development runner supplies the evidence (the reference client and the replay tests).

## Formal Methods Applicability

Not selected. If the change feed's ordering and resume rules prove subtle, the cursor and retention invariants are candidates for a bounded model; record it here when that happens.
