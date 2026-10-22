---
ddx:
  id: concerns
  type: concerns
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-21
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
---

# Project Concerns

Project Concerns declare active cross-cutting context for downstream work. They are not principles, requirements, ADRs, test plans, or implementation tasks.

tether is a library with two small reference clients. Slots that belong to a deployed web application (`frontend-framework`, `deploy-target`, `e2e-framework`, `auth-provider`, `datastore`) have no filler: the library publishes to a package index, its reference clients are a command-line program and a terminal UI, and it stores sessions through a port. This is a recorded assumption; revisit it if a web client is added.

## Active Concerns

| Concern | Source | Areas | Why Active | Key Practices |
|---------|--------|-------|------------|---------------|
| `python-uv` | library; slot `language-runtime`; operator-override (`concerns.local.yml`) | `area:core`, `area:sessions`, `area:client-api`, `area:telemetry`, `area:testkit`, `area:cli`, `area:tui` | The library and clients are Python. | `uv` for environments, locks, and builds; `pyproject.toml` for all metadata; `ruff check` and `ruff format --check` and `pyright` gate every change; `pytest` with branch coverage and `hypothesis`; the package is typed (`py.typed`); a library's `requires-python` is the lowest runtime a consumer runs (3.11, an assumption recorded in the PRD) and CI tests on it |
| `hexagonal-architecture` | library; slot `architecture-style`; operator-override (`concerns.local.yml`); ADR-003 | all | The salient fact is one core driven by many interfaces (reference clients, a consumer's server, tests) and driving many adapters (models, stores, telemetry, policy). Ports are the product. | The core imports only its own types and ports; every external system is an adapter behind a port the core owns; the application is the composition root; every port has a dummy and a conformance suite run against every implementation (including the dummy) |
| `modularity-and-encapsulation` | library | all | Boundaries are what the project sells; a boundary no check enforces erodes. | Architecture records module ownership, public surfaces, allowed and forbidden imports, integration owners, construction policy, and a boundary command; the command runs locally, in the pre-commit hook, and in CI; boundary negative controls exist |
| `concurrency-model` | library | `area:core`, `area:sessions`, `area:client-api` | The library is asynchronous throughout (ADR-001) and serves many sessions on one event loop. | One event loop; plain functions offloaded to a bounded thread pool with context copied; one turn per session; a slow consumer never blocks the loop (bounded queues with coalescing); cancellation is structured and leaves the session consistent; no blocking call inside `async def` |
| `testing` | library | all | The value of the library is that the loop is testable alone. | Stubs and dummies over mocks; replay of recorded fabricated conversations; property tests for the gate and cursor invariants; conformance suites per port; benchmarks with ratchets for the client API; a test is never skipped or deleted to hide a regression; no `time.sleep` in a test |
| `o11y-otel` | library | `area:telemetry` | Telemetry is a feature: spans and audit entries for every turn, model call, and tool call. | Applicable sections: agent run diagnostics and GenAI operations (the model-call spans); the tracer is a port with a no-op default and an OpenTelemetry (OTel) adapter as an extra; context propagates across awaits; spans and audit entries carry identifiers, sizes, and reasons and never message or tool content; service-only obligations (endpoints, SLOs) do not apply to a library |
| `resilience` | library | `area:core`, `area:sessions` | Models and stores fail; the loop must fail in defined ways. | Gates fail closed; one layer retries and owns the policy; every external call has a bound; a failed step is recorded and a turn ends with a typed stop reason; crash recovery is idempotent |
| `security-owasp` | library | all | The library handles identity per call and runs tool code. | No credential or private datum in the repository, fixtures, cassettes, or logs; the recorder strips identity and accepts only fabricated prompts; library errors never echo response bodies; dependencies pinned and audited; inputs from models validated before use |
| `scope-discipline` | library | all | The non-goals are the main defense against turning into a framework. | Each feature traces to the vision; an adjacency named in the non-goals needs an operator decision before it is built |
| `code-shape-ceilings` | library | all | Small modules are what coding agents and reviewers read and edit reliably. | Global size and complexity ceilings checked in CI with no per-file exceptions; the ceiling is lowered as files are split, never raised to pass a change |

## Project Overrides

| Concern | Practice | Override | Authority |
|---------|----------|----------|-----------|
| `python-uv` | Configuration: one central `pydantic-settings` `BaseSettings` class | The core defines plain `pydantic` configuration models and reads no environment variable, so the core does not depend on `pydantic-settings`. `pydantic-settings` is used only by the reference clients, as their composition root. | ADR-003 |
| `python-uv` | Python 3.12 or later when the runtime allows | The floor is 3.11 until the first downstream consumer's runtime is confirmed (a library's floor is the lowest consumer runtime) | Needs confirmation (PRD open assumption) |

## Area Labels

This project uses the following area labels for concern scoping:

- `area:core` — types, ports, the loop, the tool pipeline, the gates
- `area:sessions` — the entry log, stores, the turn lease, recovery
- `area:client-api` — the UI boundary: list, read, follow, commands
- `area:telemetry` — spans, audit entries, usage accounting
- `area:testkit` — dummies, conformance suites, cassettes, benchmarks
- `area:cli` — the command-line reference client
- `area:tui` — the terminal UI reference client
- `area:docs` — HELIX artifacts and user documentation

## Concern Conflicts

| Conflict | Resolution |
|----------|------------|
| `python-uv` configuration practice vs the core reading no environment | The core takes configuration objects; the clients' composition root uses `pydantic-settings` (override above) |
| `concurrency-model` (async only) vs tool authors who write plain functions | The engine offloads plain functions to worker threads; `async def` remains first-class (ADR-001) |
| `testing` (replay) vs `security-owasp` (no private data) | Recording accepts only fabricated prompts and writes only what the port carries (CONTRACT-001) |

## Observability Applicability

`o11y-otel` applies for agent run diagnostics (turn, model-call, and tool-call spans and the audit record) and GenAI operations (model-call attributes such as tier, token usage, and request id). It does not apply as a service signal: there is no endpoint or deployed service. A development runner supplies the evidence (the reference client and the replay tests).

## Formal Methods Applicability

Not selected. If the change feed's ordering and resume rules prove subtle, the cursor and retention invariants are candidates for a bounded model; record it here when that happens.
